"""
Backend del panel web de la revisión técnico-administrativa. Corre dentro de
WSL, donde ya está montada la pantalla virtual + VNC que se probó en esta
misma sesión, y sirve tanto la interfaz web como el WebSocket que transmite
los logs en vivo de cada verificación.

No reemplaza la lógica de negocio: sigue llamando a los mismos scripts de
siempre (preparar_personas.py y los 5 automation_*.py), solo que ahora vía
este servidor en vez de la consola o Streamlit.
"""
import asyncio
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

DIRECTORIO_PANEL = Path(__file__).resolve().parent.parent
DIRECTORIO_SCRIPTS = DIRECTORIO_PANEL.parent
# Raíz por defecto del explorador: donde viven las carpetas de cada
# convocatoria (REV_TEC_ADM_...), no la del proyecto en sí.
RAIZ_EXPLORADOR = DIRECTORIO_SCRIPTS.parent

sys.path.insert(0, str(DIRECTORIO_SCRIPTS))
import preparar_personas as pp  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(DIRECTORIO_SCRIPTS / ".env")
plantillas = Jinja2Templates(directory=str(DIRECTORIO_PANEL / "templates"))

VERIFICACIONES = {
    "rnmc": ("RNMC - Policía (Medidas Correctivas)", "automation_RNMC.py"),
    "contraloria": ("Contraloría - antecedentes fiscales", "automation_Contraloria.py"),
    "procuraduria": ("Procuraduría - antecedentes disciplinarios", "automation_Procuraduria.py"),
    "judicial": ("Policía - antecedentes judiciales", "automation_Judicial.py"),
    "dsex": ("Delitos sexuales - inhabilidad", "automation_DelitosSexuales.py"),
}

app = FastAPI(title="Panel de revisión técnico-administrativa")

# Subproceso de la verificación en curso (una a la vez; esto es una
# herramienta de un solo usuario local, no hace falta manejar concurrencia).
PROCESO_ACTUAL: asyncio.subprocess.Process | None = None


@app.get("/")
def pagina_logs(request: Request):
    return plantillas.TemplateResponse(request, "logs.html", {"pagina_activa": "verificaciones"})


@app.get("/api/verificaciones")
def listar_verificaciones():
    return {codigo: nombre for codigo, (nombre, _) in VERIFICACIONES.items()}


@app.get("/api/navegar")
def navegar_carpetas(ruta: str = "", extensiones: str = "csv"):
    """
    Explorador de archivos simplificado: el navegador no puede abrir el
    diálogo nativo de Windows porque la página corre en el servidor, así
    que en su lugar el propio backend (que sí tiene acceso al disco)
    devuelve el contenido de una carpeta para que el usuario navegue con
    clics en vez de escribir la ruta a mano.

    `extensiones` es una lista separada por comas (ej. "csv" o "pdf") de qué
    tipo de archivo mostrar, además de las carpetas.
    """
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
    """ok / alerta / pendiente, buscando el documento en el nombre del
    archivo (más robusto que reconstruir el nombre exacto, que depende de
    cómo se haya limpiado el primer nombre)."""
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
def abrir_carpeta(datos: dict = Body(...)):
    ruta = (datos.get("ruta") or "").strip()
    if not ruta or not os.path.isdir(ruta):
        return JSONResponse({"error": f"No es una carpeta válida: {ruta}"}, status_code=400)

    ruta_windows = _a_ruta_windows(ruta)
    try:
        # explorer.exe casi siempre devuelve un código de salida distinto de
        # 0 aunque sí haya abierto la ventana -- es un comportamiento normal
        # de Windows, no un error real, así que no se revisa el resultado.
        subprocess.Popen(["explorer.exe", ruta_windows])
        return {"abierta": True}
    except Exception as error:
        return JSONResponse({"error": str(error)}, status_code=500)


@app.get("/panorama")
def pagina_panorama(request: Request):
    return plantillas.TemplateResponse(request, "panorama.html", {"pagina_activa": "panorama"})


@app.get("/api/panorama")
def obtener_panorama(carpeta_raiz: str):
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


@app.get("/preparar")
def pagina_preparar_personas(request: Request):
    return plantillas.TemplateResponse(request, "preparar.html", {"pagina_activa": "preparar"})


@app.post("/api/preparar")
def preparar_personas(datos: dict = Body(...)):
    """
    Lee el PDF de autorización (y, si se dio, el de copias de cédula),
    concilia las fechas y devuelve las filas listas para revisar en la
    tabla -- el mismo trabajo que hacía la página de Streamlit, ahora
    servido como JSON.
    """
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
            # No se detiene todo el flujo por un problema leyendo las cédulas
            # (p. ej. una página rara): se sigue sin esas fechas y quedan
            # marcadas para revisión manual, igual que si no se hubiera dado
            # el archivo.
            print(f"Aviso: no se pudo leer el PDF de cédulas por completo: {error}")

    df_final = pp.conciliar(df, fechas_cedulas)
    ruta_salida_csv = os.path.join(os.path.dirname(ruta_autorizacion), "personas_preparadas.csv")

    return {
        "filas": df_final[COLUMNAS_EDITABLES].to_dict(orient="records"),
        "ruta_salida_csv": ruta_salida_csv,
        "total_revisar": int((df_final["REVISAR"] == "SI").sum()),
    }


@app.post("/api/guardar-csv")
def guardar_csv(datos: dict = Body(...)):
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


@app.get("/configuracion")
def pagina_configuracion(request: Request):
    return plantillas.TemplateResponse(request, "configuracion.html", {"pagina_activa": "configuracion"})


@app.get("/api/configuracion")
def obtener_configuracion():
    """
    Información real de diagnóstico, no ajustes inventados: si la clave de
    2Captcha está configurada (sin mostrarla completa), dónde quedan los
    modelos de OCR, y qué versiones de las piezas clave están instaladas.
    """
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
    }


@app.post("/api/detener")
def detener_verificacion():
    global PROCESO_ACTUAL
    if PROCESO_ACTUAL is not None and PROCESO_ACTUAL.returncode is None:
        PROCESO_ACTUAL.terminate()
        return {"detenido": True}
    return {"detenido": False, "mensaje": "No hay ninguna verificación corriendo."}


@app.websocket("/ws/ejecutar")
async def ejecutar_verificacion(websocket: WebSocket):
    """
    El cliente manda un JSON inicial {"codigo": "rnmc", "ruta_csv": "..."}
    y de ahí en adelante recibe cada línea de salida del script en vivo,
    igual que se vería en la consola.
    """
    global PROCESO_ACTUAL
    await websocket.accept()
    try:
        datos = await websocket.receive_json()
        codigo = datos.get("codigo")
        ruta_csv = (datos.get("ruta_csv") or "").strip()

        if codigo not in VERIFICACIONES:
            await websocket.send_text(f"ERROR: verificación desconocida '{codigo}'")
            await websocket.close()
            return

        if not ruta_csv or not os.path.isfile(ruta_csv):
            await websocket.send_text(f"ERROR: no se encontró el archivo: {ruta_csv}")
            await websocket.close()
            return

        nombre, script = VERIFICACIONES[codigo]
        ruta_script = DIRECTORIO_SCRIPTS / script

        await websocket.send_text(f"=== Iniciando: {nombre} ===")

        proceso = await asyncio.create_subprocess_exec(
            sys.executable, str(ruta_script), ruta_csv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(DIRECTORIO_SCRIPTS),
            env={**os.environ, "PYTHONUNBUFFERED": "1", "DISPLAY": ":1"},
        )
        PROCESO_ACTUAL = proceso

        assert proceso.stdout is not None
        async for linea_bytes in proceso.stdout:
            linea = linea_bytes.decode("utf-8", errors="replace").rstrip("\n")
            await websocket.send_text(linea)

        codigo_salida = await proceso.wait()
        if codigo_salida == 0:
            await websocket.send_text("=== Completado ===")
        elif codigo_salida < 0:
            await websocket.send_text("=== Detenido por el usuario ===")
        else:
            await websocket.send_text(f"=== Terminó con errores (código {codigo_salida}) ===")

    except WebSocketDisconnect:
        pass
    finally:
        PROCESO_ACTUAL = None
        try:
            await websocket.close()
        except Exception:
            pass
