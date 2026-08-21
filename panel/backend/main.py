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

# Prefijo corto para el .txt de log de cada verificación, igual al que usan
# los automation_*.py para sus carpetas Cert_XXX.
CODIGOS_ARCHIVO_LOG = {
    "rnmc": "RNMC",
    "contraloria": "CONT",
    "procuraduria": "PROC",
    "judicial": "JUD",
    "dsex": "DSEX",
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


# El Excel de postulantes lo sube la misma persona (o un compañero): ya lo
# tiene en su equipo, así que no tiene sentido ofrecerlo de nuevo entre los
# resultados descargables -- ahí lo que sirve es el reporte de fallidos.
ARCHIVOS_ENTRADA_OCULTOS = {"POSTULANTES.xlsx", "POSTULANTES.xls"}

ENTIDADES = [
    ("RNMC", "RNMC"),
    ("CONT", "Contraloría"),
    ("PROC", "Procuraduría"),
    ("JUD", "Judicial"),
    ("DSEX", "Delitos sexuales"),
]


def _estado_persona(carpeta_lote, codigo, doc):
    carpeta_normal = carpeta_lote / f"Cert_{codigo}"
    carpeta_alerta = carpeta_lote / f"Cert_{codigo}_INHABILITADOS"

    if carpeta_alerta.is_dir():
        for archivo in carpeta_alerta.glob("*.pdf"):
            if doc in archivo.stem:
                return "alerta"
    if carpeta_normal.is_dir():
        for archivo in carpeta_normal.glob("*.pdf"):
            if doc in archivo.stem:
                return "ok"
    return "pendiente"


def _nombre_persona(fila):
    partes = [
        str(fila.get("PRIMER_NOMBRE", "") or "").strip(),
        str(fila.get("SEGUNDO_NOMBRE", "") or "").strip(),
        str(fila.get("PRIMER_APELLIDO", "") or "").strip(),
        str(fila.get("SEGUNDO_APELLIDO", "") or "").strip(),
    ]
    nombre = " ".join(p for p in partes if p)
    return nombre or str(fila.get("DOC", ""))


def _info_lote_panorama(carpeta_lote, propietario):
    ruta_csv = carpeta_lote / "personas_preparadas.csv"
    item = {
        "nombre": carpeta_lote.name,
        "propietario": propietario,
        "preparado": ruta_csv.is_file(),
    }

    if ruta_csv.is_file():
        try:
            df_grupo = pd.read_csv(ruta_csv, dtype={"DOC": str}).fillna("")
            pendientes_revision = int((df_grupo["REVISAR"] == "SI").sum()) if "REVISAR" in df_grupo.columns else 0

            entidades = {codigo: {"etiqueta": etiqueta, "alertas": 0, "ok": 0, "total": 0} for codigo, etiqueta in ENTIDADES}
            personas = []
            for _, fila in df_grupo.iterrows():
                doc = str(fila["DOC"])
                estados_persona = {}
                for codigo, _etiqueta in ENTIDADES:
                    estado = _estado_persona(carpeta_lote, codigo, doc)
                    estados_persona[codigo] = estado
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
        except Exception as error:
            item["error"] = f"No se pudo leer personas_preparadas.csv: {error}"

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
    codigo = None
    nombre_verificacion = None
    lineas_log: list[str] = []
    marca_inicio = None
    marca_fin = None
    estado = "desconectado"
    codigo_salida = None
    try:
        datos = await websocket.receive_json()
        codigo = datos.get("codigo")
        nombre_lote = (datos.get("nombre_lote") or "").strip()
        archivo = (datos.get("archivo") or "").strip()

        if codigo not in VERIFICACIONES:
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

        nombre_verificacion, script = VERIFICACIONES[codigo]
        ruta_script = DIRECTORIO_SCRIPTS / script
        marca_inicio = datetime.now()

        for linea_encabezado in (
            f"=== Iniciando: {nombre_verificacion} ===",
            f"Hora de inicio: {marca_inicio.strftime('%Y-%m-%d %H:%M:%S')}",
        ):
            lineas_log.append(linea_encabezado)
            await websocket.send_json({"tipo": "log", "texto": linea_encabezado})

        proceso = await asyncio.create_subprocess_exec(
            sys.executable, str(ruta_script), str(ruta_datos),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(DIRECTORIO_SCRIPTS),
            env={**os.environ, "PYTHONUNBUFFERED": "1", "DISPLAY": f":{slot}"},
        )
        PROCESOS_ACTIVOS[usuario] = proceso

        assert proceso.stdout is not None
        async for linea_bytes in proceso.stdout:
            linea = linea_bytes.decode("utf-8", errors="replace").rstrip("\n")
            lineas_log.append(linea)
            await websocket.send_json({"tipo": "log", "texto": linea})

        codigo_salida = await proceso.wait()
        if codigo_salida == 0:
            estado = "completado"
        elif codigo_salida < 0:
            estado = "detenido"
        else:
            estado = "error"

        marca_fin = datetime.now()
        for linea_pie in (
            f"Hora de finalización: {marca_fin.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Tiempo de ejecución: {_formatear_duracion(marca_fin - marca_inicio)}",
        ):
            lineas_log.append(linea_pie)
            await websocket.send_json({"tipo": "log", "texto": linea_pie})

        await websocket.send_json({"tipo": "fin", "estado": estado, "codigo": codigo_salida})

    except WebSocketDisconnect:
        pass
    finally:
        PROCESOS_ACTIVOS.pop(usuario, None)
        if slot is not None:
            cupos.devolver(slot)

        # Se guarda un .txt por verificación junto a los demás resultados de
        # la revisión, para no depender de alcanzar a leer o copiar el
        # registro en vivo antes de que la pantalla pase a otra cosa.
        if carpeta_lote is not None and lineas_log:
            try:
                codigo_corto = CODIGOS_ARCHIVO_LOG.get(codigo, (codigo or "verificacion").upper())
                encabezado = f"{nombre_verificacion or codigo_corto}\n"
                pie = f"\nEstado: {estado}"
                if codigo_salida is not None:
                    pie += f" (código de salida {codigo_salida})"
                # Las horas de inicio/fin y la duración ya quedan dentro de
                # lineas_log (las mismas líneas que se ven en vivo), así que
                # el encabezado y el pie del .txt no las repiten.
                contenido = encabezado + "\n" + "\n".join(lineas_log) + "\n" + pie + "\n"
                (carpeta_lote / f"Log_{codigo_corto}.txt").write_text(contenido, encoding="utf-8")
            except Exception:
                pass

        try:
            await websocket.close()
        except Exception:
            pass
