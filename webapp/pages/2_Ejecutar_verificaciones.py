"""
Página 2: corre los 5 scripts de verificación contra el CSV ya preparado,
mostrando el progreso de cada uno en vivo en vez de tener que alternar entre
ventanas de consola.
"""
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st

DIRECTORIO_WEBAPP = Path(__file__).resolve().parent.parent
DIRECTORIO_SCRIPTS = DIRECTORIO_WEBAPP.parent
sys.path.insert(0, str(DIRECTORIO_SCRIPTS))
sys.path.insert(0, str(DIRECTORIO_WEBAPP))

from notificar import notificar_resultado_revision  # noqa: E402
from _estilos import aplicar_estilos  # noqa: E402

st.set_page_config(page_title="Ejecutar verificaciones", page_icon="✅", layout="wide")
aplicar_estilos()
st.title("✅ 2. Ejecutar verificaciones")

VERIFICACIONES = [
    ("RNMC - Policía (Medidas Correctivas)", "automation_RNMC.py"),
    ("Contraloría - antecedentes fiscales", "automation_Contraloria.py"),
    ("Procuraduría - antecedentes disciplinarios", "automation_Procuraduria.py"),
    ("Policía - antecedentes judiciales", "automation_Judicial.py"),
    ("Delitos sexuales - inhabilidad", "automation_DelitosSexuales.py"),
]


def _elegir_csv():
    try:
        import tkinter as tk
        from tkinter import filedialog

        raiz = tk.Tk()
        raiz.attributes("-topmost", True)
        raiz.withdraw()
        ruta = filedialog.askopenfilename(
            title="Selecciona el CSV ya preparado",
            filetypes=[("CSV", "*.csv"), ("Excel", "*.xlsx"), ("PDF", "*.pdf")],
        )
        raiz.destroy()
        if ruta:
            st.session_state["ruta_datos"] = ruta
    except Exception as error:
        st.error(f"No se pudo abrir el explorador de archivos: {error}")


st.session_state.setdefault("ruta_datos", "")

st.text_input(
    "Archivo con la información de los postulantes (CSV ya preparado, Excel o PDF)",
    key="ruta_datos",
)
st.button("Buscar archivo...", on_click=_elegir_csv)

st.markdown("**¿Qué verificaciones correr?**")
seleccionadas = []
cols = st.columns(len(VERIFICACIONES))
for col, (nombre, archivo) in zip(cols, VERIFICACIONES):
    with col:
        if st.checkbox(nombre, value=True, key=f"chk_{archivo}"):
            seleccionadas.append((nombre, archivo))

st.caption(
    "Cada verificación abre su propia ventana de Chrome, igual que siempre. "
    "Algunos portales piden intervención manual (pregunta de seguridad de Procuraduría, "
    "corrección de errores en Judicial / Delitos Sexuales) — atiende esa ventana cuando aparezca."
)

st.divider()

iniciar = st.button("Iniciar", type="primary", disabled=not seleccionadas)

if iniciar:
    ruta_datos = st.session_state["ruta_datos"].strip()

    if not ruta_datos or not os.path.isfile(ruta_datos):
        st.error("No se encontró el archivo indicado. Verifica la ruta.")
        st.stop()

    resultados = []

    for nombre, archivo in seleccionadas:
        st.subheader(nombre)
        estado_placeholder = st.empty()
        log_placeholder = st.empty()
        estado_placeholder.info("Corriendo...")

        ruta_script = DIRECTORIO_SCRIPTS / archivo

        # PYTHONUNBUFFERED es necesario para que la salida llegue línea por
        # línea en vivo: al escribir a una tubería (en vez de a una consola),
        # Python bufferiza por bloques por defecto, así que sin esto el
        # progreso solo aparecería todo junto al final.
        # PYTHONUTF8 asegura que el script hijo escriba de verdad en UTF-8;
        # sin esto, en Windows suele usar la página de códigos ANSI del
        # sistema al escribir a una tubería, y las tildes/eñes llegarían
        # corruptas al decodificar como UTF-8 del lado de la webapp.
        entorno = os.environ.copy()
        entorno["PYTHONUNBUFFERED"] = "1"
        entorno["PYTHONUTF8"] = "1"

        proceso = subprocess.Popen(
            [sys.executable, str(ruta_script), ruta_datos],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=entorno,
        )

        lineas = []
        for linea in proceso.stdout:
            lineas.append(linea.rstrip("\n"))
            # Se muestran solo las últimas 25 líneas para no saturar la página.
            log_placeholder.code("\n".join(lineas[-25:]) or "(sin salida todavía)")

        proceso.wait()
        exito = proceso.returncode == 0
        resultados.append((nombre, exito))

        if exito:
            estado_placeholder.success("Completado")
        else:
            estado_placeholder.error(f"Terminó con errores (código {proceso.returncode})")

        with st.expander("Ver salida completa"):
            st.code("\n".join(lineas) or "(sin salida)")

    st.divider()
    st.subheader("Resumen")
    for nombre, ok in resultados:
        if ok:
            st.success(f"{nombre}: completado")
        else:
            st.error(f"{nombre}: terminó con errores")

    nombres_totales = [nombre for nombre, _ in VERIFICACIONES]
    if notificar_resultado_revision(resultados, nombres_totales):
        st.info("Se envió un correo de aviso con el resultado.")
