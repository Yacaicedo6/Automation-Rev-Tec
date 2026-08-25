"""
Backend del panel web de la revisión técnico-administrativa. Corre dentro de
WSL, donde ya está montada la pantalla virtual + VNC que se probó en esta
misma sesión, y sirve tanto la interfaz web como el WebSocket que transmite
los logs en vivo de cada verificación.

No reemplaza la lógica de negocio: sigue llamando a los mismos scripts de
siempre (preparar_personas.py y los 5 automation_*.py), solo que ahora vía
este servidor en vez de la consola o Streamlit.

Pensado para que varias personas del equipo lo usen a la vez desde la red
local: cada una entra con su propio usuario, y "Ejecutar verificaciones"
(lo único que necesita Chrome) reparte un número limitado de "cupos" -- cada
cupo es su propia pantalla virtual + VNC, así que dos personas nunca ven ni
controlan el mismo Chrome. Ese número de cupos se lee de config.env, no
está escrito en el código, para poder subirlo el día que haya más RAM o un
servidor dedicado.
"""
import asyncio
import bcrypt
import io
import os
import re
import secrets
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import Body, FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

DIRECTORIO_PANEL = Path(__file__).resolve().parent.parent
DIRECTORIO_SCRIPTS = DIRECTORIO_PANEL.parent

# Cada persona del equipo tiene su propio espacio, aislado del de los
# demás: nadie navega la carpeta de otro, ni siquiera un administrador
# desde la app. Todo lo que alguien sube (PDFs) y todo lo que se genera
# (CSV, certificados descargados) vive solo dentro de su propia carpeta.
ESPACIOS_USUARIO = DIRECTORIO_PANEL / "espacios_usuario"
ESPACIOS_USUARIO.mkdir(exist_ok=True)


def _sanear_nombre(nombre: str, longitud_maxima: int = 80) -> str:
    """Deja solo letras, números, espacios, guiones y guion bajo. Así ni el
    usuario ni el nombre de un lote pueden convertirse en una ruta
    (por ejemplo '../../secret.key')."""
    nombre = re.sub(r"[^A-Za-z0-9 _\-]", "", (nombre or "")).strip()
    nombre = re.sub(r"\s+", " ", nombre)
    return nombre[:longitud_maxima]


def _carpeta_usuario(usuario: str) -> Path:
    carpeta = ESPACIOS_USUARIO / _sanear_nombre(usuario)
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _carpeta_lote(usuario: str, nombre_lote: str, crear: bool = False) -> Optional[Path]:
    nombre_seguro = _sanear_nombre(nombre_lote)
    if not nombre_seguro:
        return None
    carpeta = _carpeta_usuario(usuario) / nombre_seguro
    if crear:
        carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _lote_existente(usuario: str, nombre_lote: str) -> Optional[Path]:
    carpeta = _carpeta_lote(usuario, nombre_lote)
    if carpeta is None or not carpeta.is_dir():
        return None
    return carpeta


def _archivo_seguro_en_lote(carpeta_lote: Path, ruta_relativa: str) -> Optional[Path]:
    """Resuelve un nombre de archivo dentro de un lote, sin dejar que se
    escape de esa carpeta (../../ etc)."""
    try:
        destino = (carpeta_lote / ruta_relativa).resolve()
    except (OSError, RuntimeError):
        return None
    raiz = carpeta_lote.resolve()
    if destino != raiz and raiz not in destino.parents:
        return None
    return destino if destino.is_file() else None


sys.path.insert(0, str(DIRECTORIO_SCRIPTS))
sys.path.insert(0, str(DIRECTORIO_PANEL))
import preparar_personas as pp  # noqa: E402
import notificar  # noqa: E402 -- mismo mecanismo de correo que usa ejecutar_revision.py
from dotenv import load_dotenv  # noqa: E402
import gestionar_usuarios as gu  # noqa: E402 -- misma lógica que la de consola, un solo lugar

load_dotenv(DIRECTORIO_SCRIPTS / ".env")
load_dotenv(DIRECTORIO_PANEL / "config.env")
plantillas = Jinja2Templates(directory=str(DIRECTORIO_PANEL / "templates"))

MAX_SESIONES_PARALELAS = int(os.getenv("MAX_SESIONES_PARALELAS", "2"))

# ==========================================
# Sesión / login
# ==========================================
RUTA_CLAVE_SECRETA = DIRECTORIO_PANEL / "secret.key"
if not RUTA_CLAVE_SECRETA.is_file():
    RUTA_CLAVE_SECRETA.write_text(secrets.token_hex(32), encoding="utf-8")
CLAVE_SECRETA = RUTA_CLAVE_SECRETA.read_text(encoding="utf-8").strip()

def _verificar_clave(usuario, clave):
    usuarios = gu._cargar()
    datos_usuario = usuarios.get(usuario)
    if not datos_usuario:
        return False
    return bcrypt.checkpw(clave.encode("utf-8"), datos_usuario["hash"].encode("utf-8"))


def _es_admin(usuario):
    if not usuario:
        return False
    usuarios = gu._cargar()
    return bool(usuarios.get(usuario, {}).get("es_admin"))


def _usuario_de_sesion(request: Request):
    return request.session.get("usuario")


VERIFICACIONES = {
    "rnmc": ("RNMC - Policía (Medidas Correctivas)", "automation_RNMC.py"),
    "contraloria": ("Contraloría - antecedentes fiscales", "automation_Contraloria.py"),
    "procuraduria": ("Procuraduría - antecedentes disciplinarios", "automation_Procuraduria.py"),
    "judicial": ("Policía - antecedentes judiciales", "automation_Judicial.py"),
    "dsex": ("Delitos sexuales - inhabilidad", "automation_DelitosSexuales.py"),
}

# Solo estas dos entidades ofrecen consulta por NIT (persona jurídica) --
# RNMC, Judicial y Delitos Sexuales son específicamente sobre antecedentes
# de personas naturales y no aplican aquí.
VERIFICACIONES_JURIDICAS = {
    "contjur": ("Contraloría (personas jurídicas) - antecedentes fiscales", "automation_Contraloria_Juridica.py"),
    "procjur": ("Procuraduría (personas jurídicas) - antecedentes disciplinarios", "automation_Procuraduria_Juridica.py"),
}

# Prefijo corto para el .txt de log de cada verificación, igual al que usan
# los automation_*.py para sus carpetas Cert_XXX.
CODIGOS_ARCHIVO_LOG = {
    "rnmc": "RNMC",
    "contraloria": "CONT",
    "procuraduria": "PROC",
    "judicial": "JUD",
    "dsex": "DSEX",
    "contjur": "CONTJUR",
    "procjur": "PROCJUR",
}

app = FastAPI(title="Panel de revisión técnico-administrativa")
app.add_middleware(SessionMiddleware, secret_key=CLAVE_SECRETA, max_age=60 * 60 * 12)
app.mount("/static", StaticFiles(directory=str(DIRECTORIO_PANEL / "static")), name="static")


# ==========================================
# Cupos: uno por sesión de "Ejecutar verificaciones" en curso
# ==========================================
class AdministradorCupos:
    """
    Reparte un número fijo de "cupos" (cada uno con su propia pantalla
    virtual + VNC, levantados de antemano por iniciar.sh). Si no hay cupos
    libres, la persona espera en fila -- no se inventan cupos nuevos sobre
    la marcha porque cada uno cuesta RAM real.
    """

    def __init__(self, total):
        self.total = total
        self._libres = asyncio.Queue()
        for numero in range(1, total + 1):
            self._libres.put_nowait(numero)
        self._en_espera = 0
        self._lock = asyncio.Lock()

    async def tomar(self, avisar_posicion=None):
        if self._libres.empty() and avisar_posicion is not None:
            async with self._lock:
                self._en_espera += 1
                posicion = self._en_espera
            await avisar_posicion(posicion)
        numero = await self._libres.get()
        async with self._lock:
            if self._en_espera > 0:
                self._en_espera -= 1
        return numero

    def devolver(self, numero):
        self._libres.put_nowait(numero)

    def puerto_vnc(self, numero_slot):
        # Debe coincidir con la numeración que arma panel/iniciar.sh.
        return 6079 + numero_slot


cupos = AdministradorCupos(MAX_SESIONES_PARALELAS)

# Subproceso en curso por usuario (para que "Detener" pare el de esa
# persona específica, no el de cualquiera).
PROCESOS_ACTIVOS: dict[str, asyncio.subprocess.Process] = {}


def _formatear_duracion(delta) -> str:
    segundos_totales = int(delta.total_seconds())
    horas, resto = divmod(segundos_totales, 3600)
    minutos, segundos = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos:02d}m {segundos:02d}s"
    if minutos:
        return f"{minutos}m {segundos:02d}s"
    return f"{segundos}s"


LIMITE_ZIP_CORREO_BYTES = 20 * 1024 * 1024  # deja margen bajo el tope real de Gmail (~25MB)


def _resumen_estado_verificacion(estado, total_alertas, total_fallidos):
    if estado != "completado":
        return "Terminó con un error técnico"
    if total_alertas or total_fallidos:
        return "Terminó con alertas o consultas que revisar"
    return "Completada sin novedades"


def _enviar_notificacion_verificacion(
    usuario, nombre_verificacion, nombre_lote, carpeta_lote, codigo_corto,
    estado, codigo_salida, marca_inicio, marca_fin, total_pdfs,
):
    """
    Se corre en un hilo aparte (ver asyncio.to_thread en /ws/ejecutar) para
    no bloquear el servidor mientras arma el .zip o espera a Gmail. Falla en
    silencio -- un correo que no sale no debe tumbar ni ensuciar el registro
    de la verificación en sí.
    """
    usuarios = gu._cargar()
    datos_usuario = usuarios.get(usuario, {})
    if not datos_usuario.get("avisar_por_correo"):
        return
    correo_destino = (datos_usuario.get("correo") or "").strip()
    if not correo_destino:
        return

    carpeta_alerta_dir = carpeta_lote / f"Cert_{codigo_corto}_INHABILITADOS"
    total_alertas = sum(1 for _ in carpeta_alerta_dir.glob("*.pdf")) if carpeta_alerta_dir.is_dir() else 0

    ruta_fallidos = carpeta_lote / f"Fallidos_{codigo_corto}.xlsx"
    total_fallidos = None
    if ruta_fallidos.is_file():
        try:
            total_fallidos = len(pd.read_excel(ruta_fallidos))
        except Exception:
            total_fallidos = None

    resumen = _resumen_estado_verificacion(estado, total_alertas, total_fallidos or 0)
    nombres = (datos_usuario.get("nombres") or usuario).strip()

    lineas = [
        f"Hola {nombres},",
        "",
        f'Tu verificación de {nombre_verificacion} para la revisión "{nombre_lote}" ya terminó.',
        "",
        f"Resultado: {resumen}",
        f"Hora de inicio: {marca_inicio.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Hora de finalización: {marca_fin.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Tiempo de ejecución: {_formatear_duracion(marca_fin - marca_inicio)}",
        "",
        f"PDFs generados en total: {total_pdfs}",
        f"Con alerta real: {total_alertas}",
    ]
    if total_fallidos is not None:
        lineas.append(f"Con consulta fallida: {total_fallidos}")

    adjuntos = []
    ruta_log = carpeta_lote / f"Log_{codigo_corto}.txt"
    if ruta_log.is_file():
        adjuntos.append(str(ruta_log))
    if ruta_fallidos.is_file():
        adjuntos.append(str(ruta_fallidos))
    ruta_inhabilitados = carpeta_lote / f"Inhabilitados_{codigo_corto}.xlsx"
    if ruta_inhabilitados.is_file():
        adjuntos.append(str(ruta_inhabilitados))

    ruta_zip_temporal = None
    subcarpetas = [c for c in (carpeta_lote / f"Cert_{codigo_corto}", carpeta_alerta_dir) if c.is_dir()]
    if subcarpetas:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_archivo:
            for sub in subcarpetas:
                for ruta in sub.rglob("*"):
                    if ruta.is_file():
                        zip_archivo.write(ruta, arcname=str(ruta.relative_to(carpeta_lote)))
        tamano = buffer.tell()
        if tamano <= LIMITE_ZIP_CORREO_BYTES:
            descriptor, ruta_zip_temporal = tempfile.mkstemp(suffix=f"_Cert_{codigo_corto}.zip")
            with os.fdopen(descriptor, "wb") as archivo_temp:
                archivo_temp.write(buffer.getvalue())
            adjuntos.append(ruta_zip_temporal)
        else:
            lineas.append("")
            lineas.append(f"El .zip de certificados pesa más de {LIMITE_ZIP_CORREO_BYTES // (1024 * 1024)}MB y no se pudo adjuntar -- descárgalo desde el panel.")

    lineas.append("")
    lineas.append("Yan Caicedo.")

    codigo_corto_asunto = nombre_verificacion.split(" - ")[0]
    asunto = f"Revisión técnico-administrativa: {codigo_corto_asunto}"
    asunto += " terminó con alertas" if resumen != "Completada sin novedades" else " completada"
    asunto += f" — {nombre_lote}"

    try:
        notificar.enviar_notificacion(asunto, "\n".join(lineas), adjuntos=adjuntos, destinatario=correo_destino)
    finally:
        if ruta_zip_temporal and os.path.exists(ruta_zip_temporal):
            os.remove(ruta_zip_temporal)


def _pagina_o_login(request: Request):
    """Para rutas que devuelven HTML: si no hay sesión, manda a /login."""
    usuario = _usuario_de_sesion(request)
    if not usuario:
        return None, RedirectResponse("/login", status_code=303)
    return usuario, None


def _api_o_401(request: Request):
    usuario = _usuario_de_sesion(request)
    if not usuario:
        return None, JSONResponse({"error": "Sesión no iniciada."}, status_code=401)
    return usuario, None


# ==========================================
# Login
# ==========================================
@app.get("/login")
def pagina_login(request: Request):
    if _usuario_de_sesion(request):
        return RedirectResponse("/", status_code=303)
    return plantillas.TemplateResponse(request, "login.html", {})


@app.post("/login")
async def procesar_login(request: Request):
    datos = await request.form()
    usuario = str(datos.get("usuario", "")).strip()
    clave = str(datos.get("clave", ""))

    if _verificar_clave(usuario, clave):
        request.session["usuario"] = usuario
        return RedirectResponse("/", status_code=303)

    return plantillas.TemplateResponse(
        request, "login.html", {"error": "Usuario o contraseña incorrectos."}, status_code=401,
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/registro")
def pagina_registro(request: Request):
    if _usuario_de_sesion(request):
        return RedirectResponse("/", status_code=303)
    return plantillas.TemplateResponse(request, "registro.html", {})


@app.get("/terminos")
def pagina_terminos(request: Request):
    return plantillas.TemplateResponse(request, "terminos.html", {})


@app.post("/registro")
async def procesar_registro(request: Request):
    datos = await request.form()
    usuario = str(datos.get("usuario", "")).strip()
    clave = str(datos.get("clave", ""))
    confirmacion = str(datos.get("confirmacion", ""))
    nombres = str(datos.get("nombres", "")).strip()
    apellidos = str(datos.get("apellidos", "")).strip()
    identificacion = str(datos.get("identificacion", "")).strip()
    correo = str(datos.get("correo", "")).strip()
    celular = str(datos.get("celular", "")).strip()
    acepto_terminos = datos.get("acepto_terminos") == "on"

    valores_previos = {
        "usuario_previo": usuario, "nombres_previo": nombres, "apellidos_previo": apellidos,
        "identificacion_previa": identificacion, "correo_previo": correo, "celular_previo": celular,
    }

    def _error(mensaje):
        return plantillas.TemplateResponse(
            request, "registro.html", {"error": mensaje, **valores_previos}, status_code=400,
        )

    if not usuario or len(usuario) < 3:
        return _error("El usuario debe tener al menos 3 caracteres.")
    if len(clave) < 6:
        return _error("La contraseña debe tener al menos 6 caracteres.")
    if clave != confirmacion:
        return _error("Las contraseñas no coinciden.")
    if not nombres or not apellidos or not identificacion or not correo or not celular:
        return _error("Completa nombres, apellidos, identificación, correo y celular.")
    if "@" not in correo:
        return _error("El correo no parece válido.")
    if not acepto_terminos:
        return _error("Debes aceptar los términos de uso para crear la cuenta.")

    usuarios = gu._cargar()
    if usuario in usuarios:
        return _error(f"El usuario '{usuario}' ya existe. Elige otro, o entra si ya es tuyo.")

    hash_clave = bcrypt.hashpw(clave.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    usuarios[usuario] = {
        "hash": hash_clave,
        "es_admin": False,
        "nombres": nombres,
        "apellidos": apellidos,
        "identificacion": identificacion,
        "correo": correo,
        "celular": celular,
        "acepto_terminos": True,
    }
    gu._guardar(usuarios)

    request.session["usuario"] = usuario
    return RedirectResponse("/", status_code=303)


# ==========================================
# Administración (solo usuarios marcados como es_admin)
# ==========================================
@app.get("/admin")
def pagina_admin(request: Request):
    usuario, redireccion = _pagina_o_login(request)
    if redireccion:
        return redireccion
    if not _es_admin(usuario):
        return RedirectResponse("/", status_code=303)

    usuarios = gu._cargar()
    lista = [
        {
            "usuario": u,
            "es_admin": d.get("es_admin", False),
            "nombre_completo": f"{d.get('nombres', '')} {d.get('apellidos', '')}".strip(),
            "correo": d.get("correo", ""),
        }
        for u, d in usuarios.items()
    ]
    return plantillas.TemplateResponse(request, "admin.html", {
        "pagina_activa": "admin", "usuario": usuario, "es_admin": True, "usuarios": lista,
    })


@app.post("/api/admin/restablecer-clave")
def admin_restablecer_clave(request: Request, datos: dict = Body(...)):
    usuario_actual, error = _api_o_401(request)
    if error:
        return error
    if not _es_admin(usuario_actual):
        return JSONResponse({"error": "No tienes permiso de administrador."}, status_code=403)

    usuario_objetivo = (datos.get("usuario") or "").strip()
    clave_nueva = datos.get("clave") or ""
    if len(clave_nueva) < 6:
        return JSONResponse({"error": "La contraseña debe tener al menos 6 caracteres."}, status_code=400)

    usuarios = gu._cargar()
    if usuario_objetivo not in usuarios:
        return JSONResponse({"error": f"'{usuario_objetivo}' no existe."}, status_code=404)

    usuarios[usuario_objetivo]["hash"] = bcrypt.hashpw(clave_nueva.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    gu._guardar(usuarios)
    return {"actualizado": True}


@app.post("/api/admin/eliminar-usuario")
def admin_eliminar_usuario(request: Request, datos: dict = Body(...)):
    usuario_actual, error = _api_o_401(request)
    if error:
        return error
    if not _es_admin(usuario_actual):
        return JSONResponse({"error": "No tienes permiso de administrador."}, status_code=403)

    usuario_objetivo = (datos.get("usuario") or "").strip()
    if usuario_objetivo == usuario_actual:
        return JSONResponse({"error": "No puedes eliminar tu propia cuenta desde aquí."}, status_code=400)

    usuarios = gu._cargar()
    if usuario_objetivo not in usuarios:
        return JSONResponse({"error": f"'{usuario_objetivo}' no existe."}, status_code=404)

    del usuarios[usuario_objetivo]
    gu._guardar(usuarios)
    return {"eliminado": True}


@app.post("/api/admin/hacer-admin")
def admin_hacer_admin(request: Request, datos: dict = Body(...)):
    usuario_actual, error = _api_o_401(request)
    if error:
        return error
    if not _es_admin(usuario_actual):
        return JSONResponse({"error": "No tienes permiso de administrador."}, status_code=403)

    usuario_objetivo = (datos.get("usuario") or "").strip()
    valor = bool(datos.get("es_admin", True))

    usuarios = gu._cargar()
    if usuario_objetivo not in usuarios:
        return JSONResponse({"error": f"'{usuario_objetivo}' no existe."}, status_code=404)

    usuarios[usuario_objetivo]["es_admin"] = valor
    gu._guardar(usuarios)
    return {"actualizado": True}


# ==========================================
# Páginas
# ==========================================
@app.get("/")
def pagina_logs(request: Request):
    usuario, redireccion = _pagina_o_login(request)
    if redireccion:
        return redireccion
    return plantillas.TemplateResponse(request, "verificaciones.html", {
        "pagina_activa": "verificaciones", "usuario": usuario, "es_admin": _es_admin(usuario),
        "max_sesiones": MAX_SESIONES_PARALELAS,
    })


@app.get("/preparar")
def pagina_preparar_personas(request: Request):
    usuario, redireccion = _pagina_o_login(request)
    if redireccion:
        return redireccion
    return plantillas.TemplateResponse(request, "preparar.html", {
        "pagina_activa": "preparar", "usuario": usuario, "es_admin": _es_admin(usuario),
    })


@app.get("/panorama")
def pagina_panorama(request: Request):
    usuario, redireccion = _pagina_o_login(request)
    if redireccion:
        return redireccion
    return plantillas.TemplateResponse(request, "panorama.html", {
        "pagina_activa": "panorama", "usuario": usuario, "es_admin": _es_admin(usuario),
    })


@app.get("/configuracion")
def pagina_configuracion(request: Request):
    usuario, redireccion = _pagina_o_login(request)
    if redireccion:
        return redireccion
    datos_usuario = gu._cargar().get(usuario, {})
    return plantillas.TemplateResponse(request, "configuracion.html", {
        "pagina_activa": "configuracion", "usuario": usuario, "es_admin": _es_admin(usuario),
        "mis_datos": {
            "nombres": datos_usuario.get("nombres", ""),
            "apellidos": datos_usuario.get("apellidos", ""),
            "correo": datos_usuario.get("correo", ""),
            "celular": datos_usuario.get("celular", ""),
            "avisar_por_correo": datos_usuario.get("avisar_por_correo", False),
        },
    })


@app.post("/api/mi-perfil")
def actualizar_mi_perfil(request: Request, datos: dict = Body(...)):
    usuario, error = _api_o_401(request)
    if error:
        return error

    nombres = (datos.get("nombres") or "").strip()
    apellidos = (datos.get("apellidos") or "").strip()
    correo = (datos.get("correo") or "").strip()
    celular = (datos.get("celular") or "").strip()

    if not nombres or not apellidos or not correo or not celular:
        return JSONResponse({"error": "Completa nombres, apellidos, correo y celular."}, status_code=400)
    if "@" not in correo:
        return JSONResponse({"error": "El correo no parece válido."}, status_code=400)

    usuarios = gu._cargar()
    usuarios[usuario].update({"nombres": nombres, "apellidos": apellidos, "correo": correo, "celular": celular})
    gu._guardar(usuarios)
    return {"actualizado": True}


@app.post("/api/mi-aviso-correo")
def actualizar_mi_aviso_correo(request: Request, datos: dict = Body(...)):
    usuario, error = _api_o_401(request)
    if error:
        return error

    usuarios = gu._cargar()
    usuarios[usuario]["avisar_por_correo"] = bool(datos.get("avisar_por_correo"))
    gu._guardar(usuarios)
    return {"actualizado": True}


@app.post("/api/mi-clave")
def cambiar_mi_clave(request: Request, datos: dict = Body(...)):
    usuario, error = _api_o_401(request)
    if error:
        return error

    clave_actual = datos.get("clave_actual") or ""
    clave_nueva = datos.get("clave_nueva") or ""
    confirmacion = datos.get("confirmacion") or ""

    if not _verificar_clave(usuario, clave_actual):
        return JSONResponse({"error": "Tu contraseña actual no es correcta."}, status_code=403)
    if len(clave_nueva) < 6:
        return JSONResponse({"error": "La contraseña nueva debe tener al menos 6 caracteres."}, status_code=400)
    if clave_nueva != confirmacion:
        return JSONResponse({"error": "Las contraseñas no coinciden."}, status_code=400)

    usuarios = gu._cargar()
    usuarios[usuario]["hash"] = bcrypt.hashpw(clave_nueva.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    gu._guardar(usuarios)
    return {"actualizado": True}


# ==========================================
# API: verificaciones / explorador / abrir carpeta
# ==========================================
@app.get("/api/verificaciones")
def listar_verificaciones(request: Request):
    _, error = _api_o_401(request)
    if error:
        return error
    return {codigo: nombre for codigo, (nombre, _) in VERIFICACIONES.items()}


@app.get("/api/verificaciones-juridicas")
def listar_verificaciones_juridicas(request: Request):
    _, error = _api_o_401(request)
    if error:
        return error
    return {codigo: nombre for codigo, (nombre, _) in VERIFICACIONES_JURIDICAS.items()}


@app.get("/api/mis-lotes")
def listar_mis_lotes(request: Request):
    usuario, error = _api_o_401(request)
    if error:
        return error

    lotes = []
    for carpeta in sorted(_carpeta_usuario(usuario).iterdir(), key=lambda p: p.name.lower()):
        if not carpeta.is_dir():
            continue
        ruta_csv = carpeta / "personas_preparadas.csv"
        ruta_pdf = carpeta / "AUT_CONS_ANTEC.pdf"
        ruta_excel = next((f for f in (carpeta / "POSTULANTES.xlsx", carpeta / "POSTULANTES.xls") if f.is_file()), None)
        total_personas = None
        if ruta_csv.is_file():
            try:
                total_personas = len(pd.read_csv(ruta_csv, dtype={"DOC": str}))
            except Exception:
                total_personas = None

        # Para "Ejecutar verificaciones": si ya hay CSV preparado se usa ese
        # (trae las correcciones manuales); si no, se puede correr directo
        # sobre el PDF de autorización o, en su defecto, sobre el Excel de
        # postulantes subido directo -- los scripts saben leer cualquiera.
        if ruta_csv.is_file():
            archivo_para_verificar = ruta_csv.name
        elif ruta_pdf.is_file():
            archivo_para_verificar = ruta_pdf.name
        elif ruta_excel is not None:
            archivo_para_verificar = ruta_excel.name
        else:
            archivo_para_verificar = None

        lotes.append({
            "nombre_lote": carpeta.name,
            "tiene_csv": ruta_csv.is_file(),
            "total_personas": total_personas,
            "tiene_autorizacion": ruta_pdf.is_file(),
            "tiene_excel": ruta_excel is not None,
            "archivo_para_verificar": archivo_para_verificar,
        })
    return {"lotes": lotes}


@app.get("/api/mis-lotes-juridicas")
def listar_mis_lotes_juridicas(request: Request):
    """Igual que /api/mis-lotes, pero solo las revisiones que tienen un
    Excel de personas jurídicas (JURIDICAS.xlsx) -- para el selector de
    "usar una revisión jurídica mía" en Ejecutar verificaciones."""
    usuario, error = _api_o_401(request)
    if error:
        return error

    lotes = []
    for carpeta in sorted(_carpeta_usuario(usuario).iterdir(), key=lambda p: p.name.lower()):
        if not carpeta.is_dir():
            continue
        ruta_juridicas = next((f for f in (carpeta / "JURIDICAS.xlsx", carpeta / "JURIDICAS.xls") if f.is_file()), None)
        if ruta_juridicas is None:
            continue
        total_personas = None
        try:
            total_personas = len(_leer_juridicas_excel_panorama(ruta_juridicas))
        except Exception:
            total_personas = None
        lotes.append({
            "nombre_lote": carpeta.name,
            "total_personas": total_personas,
            "archivo_para_verificar": ruta_juridicas.name,
        })
    return {"lotes": lotes}


@app.get("/api/lote/{nombre_lote}/archivos")
def listar_archivos_lote(request: Request, nombre_lote: str):
    usuario, error = _api_o_401(request)
    if error:
        return error

    carpeta_lote = _lote_existente(usuario, nombre_lote)
    if carpeta_lote is None:
        return JSONResponse({"error": "Esa revisión no existe."}, status_code=404)

    archivos = [
        {"ruta_relativa": str(ruta.relative_to(carpeta_lote)), "tamano_kb": round(ruta.stat().st_size / 1024, 1)}
        for ruta in sorted(carpeta_lote.rglob("*"))
        if ruta.is_file() and ruta.name not in ARCHIVOS_ENTRADA_OCULTOS
    ]
    return {"nombre_lote": carpeta_lote.name, "archivos": archivos}


@app.get("/api/lote/{nombre_lote}/descargar")
def descargar_archivo_lote(request: Request, nombre_lote: str, archivo: str):
    usuario, error = _api_o_401(request)
    if error:
        return error

    carpeta_lote = _lote_existente(usuario, nombre_lote)
    if carpeta_lote is None:
        return JSONResponse({"error": "Esa revisión no existe."}, status_code=404)

    ruta_archivo = _archivo_seguro_en_lote(carpeta_lote, archivo)
    if ruta_archivo is None:
        return JSONResponse({"error": "Archivo no encontrado."}, status_code=404)

    return FileResponse(ruta_archivo, filename=ruta_archivo.name)


@app.get("/api/lote/{nombre_lote}/descargar-zip")
def descargar_zip_lote(request: Request, nombre_lote: str):
    usuario, error = _api_o_401(request)
    if error:
        return error

    carpeta_lote = _lote_existente(usuario, nombre_lote)
    if carpeta_lote is None:
        return JSONResponse({"error": "Esa revisión no existe."}, status_code=404)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_archivo:
        for ruta in carpeta_lote.rglob("*"):
            if ruta.is_file() and ruta.name not in ARCHIVOS_ENTRADA_OCULTOS:
                zip_archivo.write(ruta, arcname=str(ruta.relative_to(carpeta_lote)))
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{carpeta_lote.name}.zip"'},
    )


@app.get("/api/lote/{nombre_lote}/descargar-carpeta")
def descargar_carpeta_lote(request: Request, nombre_lote: str, carpeta: str):
    """Descarga solo una entidad (por ejemplo Cert_RNMC + Cert_RNMC_INHABILITADOS)
    en vez del .zip completo de la revisión -- para no arrastrar entidades que
    ya se descargaron antes cada vez que termina una verificación nueva."""
    usuario, error = _api_o_401(request)
    if error:
        return error

    carpeta_lote = _lote_existente(usuario, nombre_lote)
    if carpeta_lote is None:
        return JSONResponse({"error": "Esa revisión no existe."}, status_code=404)

    nombre_carpeta_seguro = _sanear_nombre(carpeta)
    if not nombre_carpeta_seguro:
        return JSONResponse({"error": "Carpeta no válida."}, status_code=400)

    raiz = carpeta_lote.resolve()
    subcarpetas = [
        c for c in (carpeta_lote / nombre_carpeta_seguro, carpeta_lote / f"{nombre_carpeta_seguro}_INHABILITADOS")
        if c.is_dir() and c.resolve().parent == raiz
    ]
    if not subcarpetas:
        return JSONResponse({"error": "Esa carpeta no existe en esta revisión."}, status_code=404)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_archivo:
        for sub in subcarpetas:
            for ruta in sub.rglob("*"):
                if ruta.is_file():
                    zip_archivo.write(ruta, arcname=str(ruta.relative_to(carpeta_lote)))
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre_carpeta_seguro}.zip"'},
    )


@app.post("/api/lote/{nombre_lote}/eliminar-archivo")
def eliminar_archivo_lote(request: Request, nombre_lote: str, datos: dict = Body(...)):
    """Borra un solo certificado (por ejemplo uno mal nombrado o con un
    error puntual) dentro de una revisión propia -- esa persona vuelve a
    quedar pendiente para esa entidad, sin tocar a nadie más."""
    usuario, error = _api_o_401(request)
    if error:
        return error

    carpeta_lote = _lote_existente(usuario, nombre_lote)
    if carpeta_lote is None:
        return JSONResponse({"error": "Esa revisión no existe."}, status_code=404)

    ruta_archivo = _archivo_seguro_en_lote(carpeta_lote, (datos.get("archivo") or "").strip())
    if ruta_archivo is None:
        return JSONResponse({"error": "Archivo no encontrado."}, status_code=404)

    ruta_archivo.unlink()
    return {"eliminado": True}


@app.post("/api/lote/{nombre_lote}/eliminar-carpeta")
def eliminar_carpeta_lote(request: Request, nombre_lote: str, datos: dict = Body(...)):
    """Borra los certificados de una sola entidad (Cert_XXX y su
    _INHABILITADOS) dentro de una revisión propia, para poder volver a
    correr esa verificación desde cero sin que "ya existe" bloquee el
    reintento -- por ejemplo, si un problema del portal dejó certificados
    vacíos o mal clasificados."""
    usuario, error = _api_o_401(request)
    if error:
        return error

    carpeta_lote = _lote_existente(usuario, nombre_lote)
    if carpeta_lote is None:
        return JSONResponse({"error": "Esa revisión no existe."}, status_code=404)

    nombre_carpeta_seguro = _sanear_nombre((datos.get("carpeta") or "").strip())
    if not nombre_carpeta_seguro:
        return JSONResponse({"error": "Carpeta no válida."}, status_code=400)

    raiz = carpeta_lote.resolve()
    subcarpetas = [
        c for c in (carpeta_lote / nombre_carpeta_seguro, carpeta_lote / f"{nombre_carpeta_seguro}_INHABILITADOS")
        if c.is_dir() and c.resolve().parent == raiz
    ]
    if not subcarpetas:
        return JSONResponse({"error": "Esa carpeta no existe en esta revisión."}, status_code=404)

    for sub in subcarpetas:
        shutil.rmtree(sub)

    # También se borran el .txt de log y el Excel de fallidos de esa
    # entidad, para que no queden mostrando datos de la corrida que se
    # acaba de borrar.
    codigo_entidad = nombre_carpeta_seguro.replace("Cert_", "")
    for extra in (carpeta_lote / f"Log_{codigo_entidad}.txt", carpeta_lote / f"Fallidos_{codigo_entidad}.xlsx"):
        if extra.is_file():
            extra.unlink()

    return {"eliminado": True}


@app.post("/api/admin/eliminar-revision")
def admin_eliminar_revision(request: Request, datos: dict = Body(...)):
    """Borra la carpeta completa de una revisión -- de cualquier usuario, no
    solo la propia. A diferencia de todo lo demás en Administración/Panorama,
    esto sí toca datos de otra persona (aunque solo para borrarlos, nunca
    para verlos), pensado para limpiar carpetas de prueba."""
    usuario_actual, error = _api_o_401(request)
    if error:
        return error
    if not _es_admin(usuario_actual):
        return JSONResponse({"error": "No tienes permiso de administrador."}, status_code=403)

    usuario_objetivo = (datos.get("usuario") or "").strip()
    nombre_lote = (datos.get("nombre_lote") or "").strip()

    carpeta_lote = _lote_existente(usuario_objetivo, nombre_lote)
    if carpeta_lote is None:
        return JSONResponse({"error": "Esa revisión no existe."}, status_code=404)

    shutil.rmtree(carpeta_lote)
    return {"eliminado": True}


# El Excel de postulantes lo sube la misma persona (o un compañero): ya lo
# tiene en su equipo, así que no tiene sentido ofrecerlo de nuevo entre los
# resultados descargables -- ahí lo que sirve es el reporte de fallidos.
ARCHIVOS_ENTRADA_OCULTOS = {"POSTULANTES.xlsx", "POSTULANTES.xls", "JURIDICAS.xlsx", "JURIDICAS.xls"}

ENTIDADES = [
    ("RNMC", "RNMC"),
    ("CONT", "Contraloría"),
    ("PROC", "Procuraduría"),
    ("JUD", "Judicial"),
    ("DSEX", "Delitos sexuales"),
]

# Una revisión de personas jurídicas es un tipo de lote distinto (Código,
# Razón Social, NIT en vez de nombre/documento de persona natural), con solo
# estas 2 entidades -- las únicas que ofrecen consulta por NIT.
ENTIDADES_JURIDICAS = [
    ("CONTJUR", "Contraloría (PJ)"),
    ("PROCJUR", "Procuraduría (PJ)"),
]


def _mapa_certificados_entidad(carpeta_lote, codigo):
    """
    Recorre una sola vez las carpetas Cert_<codigo> y Cert_<codigo>_INHABILITADOS
    y arma {documento: (estado, ruta_relativa)} -- antes se repetía un
    listado de carpeta (glob) por cada persona de cada entidad, lo que en
    una revisión de 60+ personas hacía cientos de listados redundantes solo
    para pintar Panorama. El documento se toma del último tramo del nombre
    de archivo (después del último "_"), que es como lo arman los
    automation_*.py -- más rápido y más preciso que "el doc aparece en
    algún lado del nombre" (esto último podía confundir, por ejemplo, la
    cédula "123" con una que la contuviera como "1234567").
    """
    mapa = {}
    carpeta_normal = carpeta_lote / f"Cert_{codigo}"
    carpeta_alerta = carpeta_lote / f"Cert_{codigo}_INHABILITADOS"

    if carpeta_normal.is_dir():
        for archivo in carpeta_normal.glob("*.pdf"):
            doc = archivo.stem.rsplit("_", 1)[-1]
            mapa[doc] = ("ok", str(archivo.relative_to(carpeta_lote)))
    if carpeta_alerta.is_dir():
        for archivo in carpeta_alerta.glob("*.pdf"):
            doc = archivo.stem.rsplit("_", 1)[-1]
            mapa[doc] = ("alerta", str(archivo.relative_to(carpeta_lote)))

    return mapa


def _nombre_persona(fila):
    partes = [
        str(fila.get("PRIMER_NOMBRE", "") or "").strip(),
        str(fila.get("SEGUNDO_NOMBRE", "") or "").strip(),
        str(fila.get("PRIMER_APELLIDO", "") or "").strip(),
        str(fila.get("SEGUNDO_APELLIDO", "") or "").strip(),
    ]
    nombre = " ".join(p for p in partes if p)
    return nombre or str(fila.get("DOC", ""))


def _carpetas_certificados_existentes(carpeta_lote, entidades=ENTIDADES):
    """Entidades que ya tienen al menos un certificado descargado en esta
    revisión -- para ofrecer su descarga por separado en vez de solo el
    .zip completo."""
    codigos = []
    for codigo, _etiqueta in entidades:
        normal = carpeta_lote / f"Cert_{codigo}"
        alerta = carpeta_lote / f"Cert_{codigo}_INHABILITADOS"
        if any(c.is_dir() and any(c.glob("*.pdf")) for c in (normal, alerta)):
            codigos.append(codigo)
    return codigos


def _quitar_acentos_panorama(texto):
    return texto.translate(str.maketrans("ÁÉÍÓÚáéíóúÑñ", "AEIOUaeiouNn"))


def _columna_que_contiene_panorama(columnas, *fragmentos):
    for columna in columnas:
        normalizada = _quitar_acentos_panorama(str(columna)).upper()
        if all(fragmento in normalizada for fragmento in fragmentos):
            return columna
    return None


def _leer_postulantes_excel_panorama(ruta_excel):
    """
    Lee solo nombre y documento del Excel de postulantes (la plantilla
    simple: Código, Nombre Completo, Tipo y Número de identificación,
    Fecha de expedición) -- lo mínimo que necesita Panorama para mostrar a
    cada persona, sin duplicar toda la lógica de los automation_*.py.
    """
    try:
        encabezados = pd.read_excel(ruta_excel, sheet_name=0, header=0, nrows=0)
    except Exception:
        return pd.DataFrame()
    encabezados.columns = encabezados.columns.str.strip()
    columnas = list(encabezados.columns)

    col_nombre = _columna_que_contiene_panorama(columnas, "NOMBRE")
    col_tipo_doc = _columna_que_contiene_panorama(columnas, "TIPO")
    columnas_sin_tipo = [c for c in columnas if c != col_tipo_doc]
    col_doc = (
        _columna_que_contiene_panorama(columnas_sin_tipo, "NUMERO", "IDENTIFICACION")
        or _columna_que_contiene_panorama(columnas_sin_tipo, "IDENTIFICACION")
        or _columna_que_contiene_panorama(columnas_sin_tipo, "DOCUMENTO")
    )
    if not (col_nombre and col_doc):
        return pd.DataFrame()

    try:
        df = pd.read_excel(ruta_excel, sheet_name=0, header=0, dtype={col_doc: str})
    except Exception:
        return pd.DataFrame()
    df.columns = df.columns.str.strip()
    df = df.dropna(subset=[col_doc])

    filas = []
    for _, fila in df.iterrows():
        nombre_completo = str(fila[col_nombre]).strip() if pd.notna(fila[col_nombre]) else ""
        tokens = nombre_completo.split()
        filas.append({
            "DOC": str(fila[col_doc]).strip(),
            "PRIMER_NOMBRE": tokens[0] if tokens else "",
            "SEGUNDO_NOMBRE": "",
            "PRIMER_APELLIDO": " ".join(tokens[1:]),
            "SEGUNDO_APELLIDO": "",
        })
    return pd.DataFrame(filas)


def _leer_juridicas_excel_panorama(ruta_excel):
    """
    Lee solo razón social y NIT del Excel de personas jurídicas (Código,
    Razón Social, Tipo de identificación, NIT) -- lo mínimo que necesita
    Panorama para mostrarlas, sin duplicar la lógica de los
    automation_*_Juridica.py.
    """
    try:
        encabezados = pd.read_excel(ruta_excel, sheet_name=0, header=0, nrows=0)
    except Exception:
        return pd.DataFrame()
    encabezados.columns = encabezados.columns.str.strip()
    columnas = list(encabezados.columns)

    col_razon = _columna_que_contiene_panorama(columnas, "RAZON")
    col_nit = (
        _columna_que_contiene_panorama(columnas, "NIT")
        or _columna_que_contiene_panorama(columnas, "IDENTIFICACION", "TRIBUTARIA")
        or _columna_que_contiene_panorama(columnas, "NUMERO", "IDENTIFICACION")
    )
    if not (col_razon and col_nit):
        return pd.DataFrame()

    try:
        df = pd.read_excel(ruta_excel, sheet_name=0, header=0, dtype={col_nit: str})
    except Exception:
        return pd.DataFrame()
    df.columns = df.columns.str.strip()
    df = df.dropna(subset=[col_nit])

    filas = []
    for _, fila in df.iterrows():
        nit = re.sub(r"\D", "", str(fila[col_nit])) if pd.notna(fila[col_nit]) else ""
        if not nit:
            continue
        filas.append({
            "DOC": nit,
            "PRIMER_NOMBRE": str(fila[col_razon]).strip() if pd.notna(fila[col_razon]) else "",
            "SEGUNDO_NOMBRE": "",
            "PRIMER_APELLIDO": "",
            "SEGUNDO_APELLIDO": "",
        })
    return pd.DataFrame(filas)


def _info_lote_panorama(carpeta_lote, propietario):
    """
    No depende de haber pasado por "Preparar personas": lee a quien esté
    disponible -- el CSV ya preparado, o si no el PDF de autorización, o si
    no el Excel de postulantes -- para que una revisión hecha con
    "Subir un Excel directo" también muestre su información real en vez de
    aparecer como "sin preparar".
    """
    ruta_csv = carpeta_lote / "personas_preparadas.csv"
    ruta_pdf = carpeta_lote / "AUT_CONS_ANTEC.pdf"
    ruta_excel = next((f for f in (carpeta_lote / "POSTULANTES.xlsx", carpeta_lote / "POSTULANTES.xls") if f.is_file()), None)
    ruta_juridicas = next((f for f in (carpeta_lote / "JURIDICAS.xlsx", carpeta_lote / "JURIDICAS.xls") if f.is_file()), None)

    # Una revisión de personas jurídicas (Código, Razón Social, NIT) es un
    # tipo de lote distinto al de personas naturales -- se detecta cuando no
    # hay ninguna fuente de persona natural pero sí un Excel de jurídicas, y
    # usa sus propias 2 entidades en vez de las 5 de siempre.
    es_juridica = not (ruta_csv.is_file() or ruta_pdf.is_file() or ruta_excel is not None) and ruta_juridicas is not None
    entidades_lote = ENTIDADES_JURIDICAS if es_juridica else ENTIDADES

    item = {
        "nombre": carpeta_lote.name,
        "propietario": propietario,
        "carpetas_certificados": _carpetas_certificados_existentes(carpeta_lote, entidades_lote),
        "es_juridica": es_juridica,
    }

    try:
        if es_juridica:
            df_grupo = _leer_juridicas_excel_panorama(ruta_juridicas).fillna("")
        elif ruta_csv.is_file():
            df_grupo = pd.read_csv(ruta_csv, dtype={"DOC": str}).fillna("")
        elif ruta_pdf.is_file():
            df_grupo = pp.leer_autorizaciones(str(ruta_pdf)).fillna("")
        elif ruta_excel is not None:
            df_grupo = _leer_postulantes_excel_panorama(ruta_excel).fillna("")
        else:
            df_grupo = None
    except Exception as error:
        item["error"] = f"No se pudo leer la información de postulantes: {error}"
        return item

    if df_grupo is None or df_grupo.empty:
        item["sin_datos"] = True
        return item

    pendientes_revision = int((df_grupo["REVISAR"] == "SI").sum()) if "REVISAR" in df_grupo.columns else 0

    mapas_entidad = {codigo: _mapa_certificados_entidad(carpeta_lote, codigo) for codigo, _etiqueta in entidades_lote}

    entidades = {codigo: {"etiqueta": etiqueta, "alertas": 0, "ok": 0, "total": 0} for codigo, etiqueta in entidades_lote}
    personas = []
    for _, fila in df_grupo.iterrows():
        doc = str(fila["DOC"])
        estados_persona = {}
        for codigo, _etiqueta in entidades_lote:
            estado, ruta_relativa = mapas_entidad[codigo].get(doc, ("pendiente", None))
            estados_persona[codigo] = {"estado": estado, "archivo": ruta_relativa}
            entidades[codigo]["total"] += 1
            if estado == "ok":
                entidades[codigo]["ok"] += 1
            elif estado == "alerta":
                entidades[codigo]["alertas"] += 1
        personas.append({"doc": doc, "nombre": _nombre_persona(fila), "estados": estados_persona})

    item.update({
        "total_personas": len(df_grupo),
        "pendientes_revision": pendientes_revision,
        "entidades": entidades,
        "personas": personas,
    })
    return item


@app.get("/api/panorama")
def obtener_panorama(request: Request):
    usuario, error = _api_o_401(request)
    if error:
        return error

    grupos = []
    if _es_admin(usuario):
        # El administrador ve el progreso de todo el equipo, un lote de
        # cada quien a la vez -- pero esto es solo un resumen (nombres y
        # cifras), no un explorador de archivos: no se navegan ni se abren
        # los archivos de otra persona desde aquí.
        for carpeta_usuario in sorted(ESPACIOS_USUARIO.iterdir(), key=lambda p: p.name.lower()):
            if not carpeta_usuario.is_dir():
                continue
            for carpeta_lote in sorted(carpeta_usuario.iterdir(), key=lambda p: p.name.lower()):
                if carpeta_lote.is_dir():
                    grupos.append(_info_lote_panorama(carpeta_lote, carpeta_usuario.name))
    else:
        for carpeta_lote in sorted(_carpeta_usuario(usuario).iterdir(), key=lambda p: p.name.lower()):
            if carpeta_lote.is_dir():
                grupos.append(_info_lote_panorama(carpeta_lote, usuario))

    return {"grupos": grupos, "es_admin": _es_admin(usuario)}


COLUMNAS_EDITABLES = [
    "DOC", "TIPO_DOC", "PRIMER_NOMBRE", "SEGUNDO_NOMBRE",
    "PRIMER_APELLIDO", "SEGUNDO_APELLIDO", "FECHA_EXPEDICION",
    "REVISAR", "MOTIVO_REVISAR",
]


@app.post("/api/preparar")
async def preparar_personas(
    request: Request,
    nombre_lote: str = Form(...),
    pdf_autorizacion: UploadFile = File(...),
    pdf_cedulas: Optional[UploadFile] = File(None),
):
    usuario, error = _api_o_401(request)
    if error:
        return error

    carpeta_lote = _carpeta_lote(usuario, nombre_lote, crear=True)
    if carpeta_lote is None:
        return JSONResponse({"error": "El nombre de la revisión no puede estar vacío."}, status_code=400)

    if not pdf_autorizacion.filename or not pdf_autorizacion.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "El archivo de autorización debe ser un PDF."}, status_code=400)

    ruta_autorizacion = carpeta_lote / "AUT_CONS_ANTEC.pdf"
    ruta_autorizacion.write_bytes(await pdf_autorizacion.read())

    ruta_cedulas = None
    if pdf_cedulas is not None and pdf_cedulas.filename:
        if not pdf_cedulas.filename.lower().endswith(".pdf"):
            return JSONResponse({"error": "El archivo de cédulas debe ser un PDF."}, status_code=400)
        ruta_cedulas = carpeta_lote / "CEDULAS.pdf"
        ruta_cedulas.write_bytes(await pdf_cedulas.read())

    df = pp.leer_autorizaciones(str(ruta_autorizacion))
    if df.empty:
        return JSONResponse({
            "error": "No se encontró ninguna persona en el PDF de autorización. "
                     "Puede que use una redacción distinta a las ya conocidas.",
        }, status_code=422)

    fechas_cedulas = {}
    if ruta_cedulas is not None:
        try:
            fechas_cedulas = pp.leer_fechas_desde_cedulas(str(ruta_cedulas), set(df["DOC"]))
        except Exception as error:
            print(f"Aviso: no se pudo leer el PDF de cédulas por completo: {error}")

    df_final = pp.conciliar(df, fechas_cedulas)

    return {
        "nombre_lote": carpeta_lote.name,
        "filas": df_final[COLUMNAS_EDITABLES].to_dict(orient="records"),
        "total_revisar": int((df_final["REVISAR"] == "SI").sum()),
    }


@app.post("/api/subir-pdf-directo")
async def subir_pdf_directo(request: Request, nombre_lote: str = Form(...), pdf: UploadFile = File(...)):
    """Camino rápido: correr una verificación directo desde el PDF de
    autorización, sin pasar por 'Preparar personas' -- los scripts ya
    saben leer personas directo de ese PDF."""
    usuario, error = _api_o_401(request)
    if error:
        return error

    carpeta_lote = _carpeta_lote(usuario, nombre_lote, crear=True)
    if carpeta_lote is None:
        return JSONResponse({"error": "El nombre de la revisión no puede estar vacío."}, status_code=400)
    if not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "Debe ser un PDF."}, status_code=400)

    ruta_pdf = carpeta_lote / "AUT_CONS_ANTEC.pdf"
    ruta_pdf.write_bytes(await pdf.read())

    return {"nombre_lote": carpeta_lote.name, "archivo": ruta_pdf.name}


@app.post("/api/subir-excel-directo")
async def subir_excel_directo(request: Request, nombre_lote: str = Form(...), excel: UploadFile = File(...)):
    """Camino rápido: correr una verificación directo desde el Excel de
    postulantes (Código, Nombre completo, Tipo y número de identificación,
    Fecha de expedición) -- los scripts ya saben leer personas directo de
    ese Excel."""
    usuario, error = _api_o_401(request)
    if error:
        return error

    carpeta_lote = _carpeta_lote(usuario, nombre_lote, crear=True)
    if carpeta_lote is None:
        return JSONResponse({"error": "El nombre de la revisión no puede estar vacío."}, status_code=400)

    extension = Path(excel.filename or "").suffix.lower()
    if extension not in (".xlsx", ".xls"):
        return JSONResponse({"error": "Debe ser un archivo de Excel (.xlsx o .xls)."}, status_code=400)

    ruta_excel = carpeta_lote / f"POSTULANTES{extension}"
    ruta_excel.write_bytes(await excel.read())

    return {"nombre_lote": carpeta_lote.name, "archivo": ruta_excel.name}


@app.post("/api/subir-excel-juridicas-directo")
async def subir_excel_juridicas_directo(request: Request, nombre_lote: str = Form(...), excel: UploadFile = File(...)):
    """Igual que /api/subir-excel-directo, pero para el Excel de personas
    jurídicas (Código, Razón Social, Tipo de identificación, NIT) -- se
    guarda con un nombre distinto (JURIDICAS, no POSTULANTES) para que no se
    confunda con una revisión de personas naturales."""
    usuario, error = _api_o_401(request)
    if error:
        return error

    carpeta_lote = _carpeta_lote(usuario, nombre_lote, crear=True)
    if carpeta_lote is None:
        return JSONResponse({"error": "El nombre de la revisión no puede estar vacío."}, status_code=400)

    extension = Path(excel.filename or "").suffix.lower()
    if extension not in (".xlsx", ".xls"):
        return JSONResponse({"error": "Debe ser un archivo de Excel (.xlsx o .xls)."}, status_code=400)

    ruta_excel = carpeta_lote / f"JURIDICAS{extension}"
    ruta_excel.write_bytes(await excel.read())

    return {"nombre_lote": carpeta_lote.name, "archivo": ruta_excel.name}


@app.post("/api/guardar-csv")
def guardar_csv(request: Request, datos: dict = Body(...)):
    usuario, error = _api_o_401(request)
    if error:
        return error

    nombre_lote = (datos.get("nombre_lote") or "").strip()
    filas = datos.get("filas") or []

    carpeta_lote = _lote_existente(usuario, nombre_lote)
    if carpeta_lote is None:
        return JSONResponse({"error": "Esa revisión no existe."}, status_code=404)

    ruta_csv = carpeta_lote / "personas_preparadas.csv"
    if ruta_csv.is_file():
        marca_tiempo = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(ruta_csv, carpeta_lote / f"personas_preparadas.bak_{marca_tiempo}.csv")

    pd.DataFrame(filas)[COLUMNAS_EDITABLES].to_csv(ruta_csv, index=False, encoding="utf-8-sig")

    return {"guardado": True, "nombre_lote": carpeta_lote.name}


@app.get("/api/configuracion")
def obtener_configuracion(request: Request):
    usuario, error = _api_o_401(request)
    if error:
        return error
    if not _es_admin(usuario):
        return JSONResponse({"error": "No tienes permiso de administrador."}, status_code=403)

    clave_captcha = os.getenv("API_KEY_2CAPTCHA", "")

    try:
        import torch
        version_torch = torch.__version__
    except Exception:
        version_torch = "no instalado"

    ruta_modelos_ocr = os.getenv("EASYOCR_MODULE_PATH") or "~/.EasyOCR (ubicación por defecto)"

    return {
        "captcha_configurada": bool(clave_captcha),
        "captcha_clave_parcial": f"...{clave_captcha[-4:]}" if len(clave_captcha) >= 4 else None,
        "ruta_modelos_ocr": ruta_modelos_ocr,
        "version_python": sys.version.split()[0],
        "version_torch": version_torch,
        "directorio_scripts": str(DIRECTORIO_SCRIPTS),
        "max_sesiones_paralelas": MAX_SESIONES_PARALELAS,
    }


@app.post("/api/detener")
def detener_verificacion(request: Request):
    usuario, error = _api_o_401(request)
    if error:
        return error

    proceso = PROCESOS_ACTIVOS.get(usuario)
    if proceso is not None and proceso.returncode is None:
        proceso.terminate()
        return {"detenido": True}
    return {"detenido": False, "mensaje": "No tienes ninguna verificación corriendo."}


@app.websocket("/ws/ejecutar")
async def ejecutar_verificacion(websocket: WebSocket):
    """
    El cliente manda un JSON inicial
    {"codigo": "rnmc", "nombre_lote": "...", "archivo": "personas_preparadas.csv"}
    y de ahí en adelante recibe mensajes JSON:
      {"tipo": "espera", "posicion": N}      -- mientras espera un cupo
      {"tipo": "cupo", "puerto_vnc": 6081}   -- ya tiene cupo, aquí está el VNC
      {"tipo": "log", "texto": "..."}        -- una línea de salida del script
      {"tipo": "fin", "estado": "completado"/"error"/"detenido"}
    """
    await websocket.accept()

    usuario = _usuario_de_sesion(websocket)
    if not usuario:
        await websocket.send_json({"tipo": "log", "texto": "ERROR: sesión no iniciada."})
        await websocket.close()
        return

    slot = None
    carpeta_lote = None
    nombre_lote = None
    codigo = None
    codigo_corto = None
    nombre_verificacion = None
    archivo_log = None
    marca_inicio = None
    marca_fin = None
    estado = "desconectado"
    codigo_salida = None
    total_pdfs = None

    async def _registrar(texto):
        # Se escribe (con flush) de una vez en el .txt, en vez de guardar
        # todo junto al final -- así, si el panel se cae de golpe a media
        # corrida (por ejemplo, alguien lo reinicia sin darle "Detener"
        # primero), lo que ya pasó queda guardado y no se pierde entero.
        if archivo_log is not None:
            try:
                archivo_log.write(texto + "\n")
                archivo_log.flush()
            except Exception:
                pass
        await websocket.send_json({"tipo": "log", "texto": texto})

    try:
        datos = await websocket.receive_json()
        codigo = datos.get("codigo")
        nombre_lote = (datos.get("nombre_lote") or "").strip()
        archivo = (datos.get("archivo") or "").strip()

        verificacion_encontrada = VERIFICACIONES.get(codigo) or VERIFICACIONES_JURIDICAS.get(codigo)
        if verificacion_encontrada is None:
            await websocket.send_json({"tipo": "log", "texto": f"ERROR: verificación desconocida '{codigo}'"})
            return

        carpeta_lote = _lote_existente(usuario, nombre_lote)
        ruta_datos = _archivo_seguro_en_lote(carpeta_lote, archivo) if carpeta_lote else None
        if ruta_datos is None:
            await websocket.send_json({"tipo": "log", "texto": f"ERROR: no se encontró el archivo '{archivo}' en la revisión '{nombre_lote}'."})
            return

        async def avisar_posicion(posicion):
            await websocket.send_json({"tipo": "espera", "posicion": posicion})

        slot = await cupos.tomar(avisar_posicion)
        await websocket.send_json({"tipo": "cupo", "puerto_vnc": cupos.puerto_vnc(slot)})

        nombre_verificacion, script = verificacion_encontrada
        ruta_script = DIRECTORIO_SCRIPTS / script
        marca_inicio = datetime.now()

        codigo_corto = CODIGOS_ARCHIVO_LOG.get(codigo, (codigo or "verificacion").upper())
        try:
            archivo_log = open(carpeta_lote / f"Log_{codigo_corto}.txt", "w", encoding="utf-8")
            archivo_log.write(f"{nombre_verificacion}\n\n")
            archivo_log.flush()
        except Exception:
            archivo_log = None

        await _registrar(f"=== Iniciando: {nombre_verificacion} ===")
        await _registrar(f"Hora de inicio: {marca_inicio.strftime('%Y-%m-%d %H:%M:%S')}")

        proceso = await asyncio.create_subprocess_exec(
            sys.executable, str(ruta_script), str(ruta_datos),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(DIRECTORIO_SCRIPTS),
            env={**os.environ, "PYTHONUNBUFFERED": "1", "DISPLAY": f":{slot}"},
        )
        PROCESOS_ACTIVOS[usuario] = proceso

        assert proceso.stdout is not None
        async for linea_bytes in proceso.stdout:
            linea = linea_bytes.decode("utf-8", errors="replace").rstrip("\n")
            await _registrar(linea)

        codigo_salida = await proceso.wait()
        if codigo_salida == 0:
            estado = "completado"
        elif codigo_salida < 0:
            estado = "detenido"
        else:
            estado = "error"

        marca_fin = datetime.now()
        carpeta_normal = carpeta_lote / f"Cert_{codigo_corto}"
        carpeta_alerta = carpeta_lote / f"Cert_{codigo_corto}_INHABILITADOS"
        total_pdfs = sum(1 for c in (carpeta_normal, carpeta_alerta) if c.is_dir() for _ in c.glob("*.pdf"))

        await _registrar(f"Hora de finalización: {marca_fin.strftime('%Y-%m-%d %H:%M:%S')}")
        await _registrar(f"Tiempo de ejecución: {_formatear_duracion(marca_fin - marca_inicio)}")
        await _registrar(f"PDFs generados en total: {total_pdfs}")

        await websocket.send_json({"tipo": "fin", "estado": estado, "codigo": codigo_salida})

    except WebSocketDisconnect:
        pass
    finally:
        PROCESOS_ACTIVOS.pop(usuario, None)
        if slot is not None:
            cupos.devolver(slot)

        if archivo_log is not None:
            try:
                pie = f"\nEstado: {estado}"
                if codigo_salida is not None:
                    pie += f" (código de salida {codigo_salida})"
                archivo_log.write(pie + "\n")
                archivo_log.flush()
            except Exception:
                pass
            try:
                archivo_log.close()
            except Exception:
                pass

        # El correo se manda en un hilo aparte para no congelar el servidor
        # (armar el .zip y hablar con Gmail toma su tiempo) mientras otras
        # personas siguen usando el panel.
        if carpeta_lote is not None and codigo_corto is not None and marca_fin is not None and total_pdfs is not None:
            try:
                await asyncio.to_thread(
                    _enviar_notificacion_verificacion,
                    usuario, nombre_verificacion, nombre_lote, carpeta_lote, codigo_corto,
                    estado, codigo_salida, marca_inicio, marca_fin, total_pdfs,
                )
            except Exception:
                pass

        try:
            await websocket.close()
        except Exception:
            pass
