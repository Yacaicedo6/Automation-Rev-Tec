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
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

DIRECTORIO_PANEL = Path(__file__).resolve().parent.parent
DIRECTORIO_SCRIPTS = DIRECTORIO_PANEL.parent
# Raíz por defecto del explorador: donde viven las carpetas de cada
# convocatoria (REV_TEC_ADM_...), no la del proyecto en sí.
RAIZ_EXPLORADOR = DIRECTORIO_SCRIPTS.parent

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

app = FastAPI(title="Panel de revisión técnico-administrativa")
app.add_middleware(SessionMiddleware, secret_key=CLAVE_SECRETA, max_age=60 * 60 * 12)


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


@app.post("/registro")
async def procesar_registro(request: Request):
    datos = await request.form()
    usuario = str(datos.get("usuario", "")).strip()
    clave = str(datos.get("clave", ""))
    confirmacion = str(datos.get("confirmacion", ""))

    def _error(mensaje):
        return plantillas.TemplateResponse(
            request, "registro.html", {"error": mensaje, "usuario_previo": usuario}, status_code=400,
        )

    if not usuario or len(usuario) < 3:
        return _error("El usuario debe tener al menos 3 caracteres.")
    if len(clave) < 6:
        return _error("La contraseña debe tener al menos 6 caracteres.")
    if clave != confirmacion:
        return _error("Las contraseñas no coinciden.")

    usuarios = gu._cargar()
    if usuario in usuarios:
        return _error(f"El usuario '{usuario}' ya existe. Elige otro, o entra si ya es tuyo.")

    hash_clave = bcrypt.hashpw(clave.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    usuarios[usuario] = {"hash": hash_clave, "es_admin": False}
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
    lista = [{"usuario": u, "es_admin": d.get("es_admin", False)} for u, d in usuarios.items()]
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
    return plantillas.TemplateResponse(request, "logs.html", {
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
    return plantillas.TemplateResponse(request, "configuracion.html", {
        "pagina_activa": "configuracion", "usuario": usuario, "es_admin": _es_admin(usuario),
    })


# ==========================================
# API: verificaciones / explorador / abrir carpeta
# ==========================================
@app.get("/api/verificaciones")
def listar_verificaciones(request: Request):
    _, error = _api_o_401(request)
    if error:
        return error
    return {codigo: nombre for codigo, (nombre, _) in VERIFICACIONES.items()}


@app.get("/api/navegar")
def navegar_carpetas(request: Request, ruta: str = "", extensiones: str = "csv"):
    _, error = _api_o_401(request)
    if error:
        return error

    ruta_actual = Path(ruta) if ruta else RAIZ_EXPLORADOR
    if not ruta_actual.is_dir():
        return JSONResponse({"error": f"No es una carpeta válida: {ruta_actual}"}, status_code=400)

    exts_permitidas = {f".{e.strip().lower().lstrip('.')}" for e in extensiones.split(",") if e.strip()}

    carpetas = []
    archivos = []
    try:
        for item in sorted(ruta_actual.iterdir(), key=lambda p: p.name.lower()):
            if item.is_dir():
                carpetas.append(item.name)
            elif item.suffix.lower() in exts_permitidas:
                archivos.append(item.name)
    except PermissionError:
        pass

    ruta_padre = str(ruta_actual.parent) if ruta_actual != ruta_actual.parent else None

    return {
        "ruta_actual": str(ruta_actual),
        "ruta_padre": ruta_padre,
        "carpetas": carpetas,
        "archivos": archivos,
    }


def _a_ruta_windows(ruta_wsl):
    """Convierte una ruta tipo /mnt/e/algo a E:\\algo, para poder abrirla en
    el Explorador de Windows desde este backend que corre en WSL."""
    partes = Path(ruta_wsl).parts
    if len(partes) >= 3 and partes[0] == "/" and partes[1] == "mnt" and len(partes[2]) == 1:
        letra = partes[2].upper()
        resto = "\\".join(partes[3:])
        return f"{letra}:\\{resto}" if resto else f"{letra}:\\"
    return ruta_wsl


@app.post("/api/abrir-carpeta")
def abrir_carpeta(request: Request, datos: dict = Body(...)):
    _, error = _api_o_401(request)
    if error:
        return error

    ruta = (datos.get("ruta") or "").strip()
    if not ruta or not os.path.isdir(ruta):
        return JSONResponse({"error": f"No es una carpeta válida: {ruta}"}, status_code=400)

    ruta_windows = _a_ruta_windows(ruta)
    try:
        subprocess.Popen(["explorer.exe", ruta_windows])
        return {"abierta": True}
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=500)


ENTIDADES = [
    ("RNMC", "RNMC"),
    ("CONT", "Contraloría"),
    ("PROC", "Procuraduría"),
    ("JUD", "Judicial"),
    ("DSEX", "Delitos sexuales"),
]


def _buscar_grupos(carpeta_raiz):
    raiz = Path(carpeta_raiz)
    if not raiz.is_dir():
        return []
    return sorted({ruta.parent for ruta in raiz.rglob("AUT_CONS_ANTEC.pdf")})


def _estado_persona(carpeta_grupo, codigo, doc):
    carpeta_normal = carpeta_grupo / f"Cert_{codigo}"
    carpeta_alerta = carpeta_grupo / f"Cert_{codigo}_INHABILITADOS"

    if carpeta_alerta.is_dir():
        for archivo in carpeta_alerta.glob("*.pdf"):
            if doc in archivo.stem:
                return "alerta"
    if carpeta_normal.is_dir():
        for archivo in carpeta_normal.glob("*.pdf"):
            if doc in archivo.stem:
                return "ok"
    return "pendiente"


@app.get("/api/panorama")
def obtener_panorama(request: Request, carpeta_raiz: str):
    _, error = _api_o_401(request)
    if error:
        return error

    grupos = _buscar_grupos(carpeta_raiz)
    resultado = []

    for carpeta_grupo in grupos:
        ruta_csv = carpeta_grupo / "personas_preparadas.csv"
        item = {
            "nombre": carpeta_grupo.name,
            "ruta": str(carpeta_grupo),
            "preparado": ruta_csv.is_file(),
        }

        if ruta_csv.is_file():
            try:
                df_grupo = pd.read_csv(ruta_csv, dtype={"DOC": str})
                documentos = list(df_grupo["DOC"])
                pendientes_revision = int((df_grupo["REVISAR"] == "SI").sum()) if "REVISAR" in df_grupo.columns else 0

                entidades = {}
                for codigo, etiqueta in ENTIDADES:
                    estados = [_estado_persona(carpeta_grupo, codigo, doc) for doc in documentos]
                    entidades[codigo] = {
                        "etiqueta": etiqueta,
                        "alertas": estados.count("alerta"),
                        "ok": estados.count("ok"),
                        "total": len(estados),
                    }

                item.update({
                    "total_personas": len(documentos),
                    "pendientes_revision": pendientes_revision,
                    "entidades": entidades,
                })
            except Exception as error:
                item["error"] = f"No se pudo leer personas_preparadas.csv: {error}"

        resultado.append(item)

    return {"grupos": resultado}


COLUMNAS_EDITABLES = [
    "DOC", "TIPO_DOC", "PRIMER_NOMBRE", "SEGUNDO_NOMBRE",
    "PRIMER_APELLIDO", "SEGUNDO_APELLIDO", "FECHA_EXPEDICION",
    "REVISAR", "MOTIVO_REVISAR",
]


@app.post("/api/preparar")
def preparar_personas(request: Request, datos: dict = Body(...)):
    _, error = _api_o_401(request)
    if error:
        return error

    ruta_autorizacion = (datos.get("ruta_autorizacion") or "").strip()
    ruta_cedulas = (datos.get("ruta_cedulas") or "").strip()

    if not ruta_autorizacion or not os.path.isfile(ruta_autorizacion):
        return JSONResponse({"error": "No se encontró el PDF de autorización."}, status_code=400)

    df = pp.leer_autorizaciones(ruta_autorizacion)
    if df.empty:
        return JSONResponse({
            "error": "No se encontró ninguna persona en el PDF de autorización. "
                     "Puede que use una redacción distinta a las ya conocidas.",
        }, status_code=422)

    fechas_cedulas = {}
    if ruta_cedulas and os.path.isfile(ruta_cedulas):
        try:
            fechas_cedulas = pp.leer_fechas_desde_cedulas(ruta_cedulas, set(df["DOC"]))
        except Exception as error:
            print(f"Aviso: no se pudo leer el PDF de cédulas por completo: {error}")

    df_final = pp.conciliar(df, fechas_cedulas)
    ruta_salida_csv = os.path.join(os.path.dirname(ruta_autorizacion), "personas_preparadas.csv")

    return {
        "filas": df_final[COLUMNAS_EDITABLES].to_dict(orient="records"),
        "ruta_salida_csv": ruta_salida_csv,
        "total_revisar": int((df_final["REVISAR"] == "SI").sum()),
    }


@app.post("/api/guardar-csv")
def guardar_csv(request: Request, datos: dict = Body(...)):
    _, error = _api_o_401(request)
    if error:
        return error

    ruta_csv = (datos.get("ruta_csv") or "").strip()
    filas = datos.get("filas") or []

    if not ruta_csv:
        return JSONResponse({"error": "Falta la ruta de salida."}, status_code=400)

    if os.path.isfile(ruta_csv):
        marca_tiempo = datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta_respaldo = ruta_csv.replace(".csv", f".bak_{marca_tiempo}.csv")
        shutil.copy2(ruta_csv, ruta_respaldo)

    pd.DataFrame(filas)[COLUMNAS_EDITABLES].to_csv(ruta_csv, index=False, encoding="utf-8-sig")

    return {"guardado": True, "ruta_csv": ruta_csv}


@app.get("/api/configuracion")
def obtener_configuracion(request: Request):
    _, error = _api_o_401(request)
    if error:
        return error

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
    El cliente manda un JSON inicial {"codigo": "rnmc", "ruta_csv": "..."}
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
    try:
        datos = await websocket.receive_json()
        codigo = datos.get("codigo")
        ruta_csv = (datos.get("ruta_csv") or "").strip()

        if codigo not in VERIFICACIONES:
            await websocket.send_json({"tipo": "log", "texto": f"ERROR: verificación desconocida '{codigo}'"})
            return

        if not ruta_csv or not os.path.isfile(ruta_csv):
            await websocket.send_json({"tipo": "log", "texto": f"ERROR: no se encontró el archivo: {ruta_csv}"})
            return

        async def avisar_posicion(posicion):
            await websocket.send_json({"tipo": "espera", "posicion": posicion})

        slot = await cupos.tomar(avisar_posicion)
        await websocket.send_json({"tipo": "cupo", "puerto_vnc": cupos.puerto_vnc(slot)})

        nombre, script = VERIFICACIONES[codigo]
        ruta_script = DIRECTORIO_SCRIPTS / script

        await websocket.send_json({"tipo": "log", "texto": f"=== Iniciando: {nombre} ==="})

        proceso = await asyncio.create_subprocess_exec(
            sys.executable, str(ruta_script), ruta_csv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(DIRECTORIO_SCRIPTS),
            env={**os.environ, "PYTHONUNBUFFERED": "1", "DISPLAY": f":{slot}"},
        )
        PROCESOS_ACTIVOS[usuario] = proceso

        assert proceso.stdout is not None
        async for linea_bytes in proceso.stdout:
            linea = linea_bytes.decode("utf-8", errors="replace").rstrip("\n")
            await websocket.send_json({"tipo": "log", "texto": linea})

        codigo_salida = await proceso.wait()
        if codigo_salida == 0:
            estado = "completado"
        elif codigo_salida < 0:
            estado = "detenido"
        else:
            estado = "error"
        await websocket.send_json({"tipo": "fin", "estado": estado, "codigo": codigo_salida})

    except WebSocketDisconnect:
        pass
    finally:
        PROCESOS_ACTIVOS.pop(usuario, None)
        if slot is not None:
            cupos.devolver(slot)
        try:
            await websocket.close()
        except Exception:
            pass
