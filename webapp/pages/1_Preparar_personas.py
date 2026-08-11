"""
Página 1: prepara el CSV de personas a partir del PDF de autorización y,
opcionalmente, el PDF de copias de cédula — con revisión editable en pantalla
en vez de tener que abrir el CSV en Excel.
"""
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

DIRECTORIO_WEBAPP = Path(__file__).resolve().parent.parent
DIRECTORIO_SCRIPTS = DIRECTORIO_WEBAPP.parent
sys.path.insert(0, str(DIRECTORIO_SCRIPTS))
sys.path.insert(0, str(DIRECTORIO_WEBAPP))

import preparar_personas as pp  # noqa: E402
from _estilos import aplicar_estilos  # noqa: E402

st.set_page_config(page_title="Preparar personas", page_icon="📋", layout="wide")
aplicar_estilos()
st.title("📋 1. Preparar personas")

COLUMNAS_EDITABLES = [
    "DOC", "TIPO_DOC", "PRIMER_NOMBRE", "SEGUNDO_NOMBRE",
    "PRIMER_APELLIDO", "SEGUNDO_APELLIDO", "FECHA_EXPEDICION",
    "REVISAR", "MOTIVO_REVISAR",
]


def _elegir_archivo(clave_estado, titulo, tipos):
    """Abre el explorador de Windows nativo (la app corre en tu propio
    computador, así que esto abre una ventana normal de selección de
    archivo) y guarda la ruta elegida en el estado de la página."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        raiz = tk.Tk()
        raiz.attributes("-topmost", True)
        raiz.withdraw()
        ruta = filedialog.askopenfilename(title=titulo, filetypes=tipos)
        raiz.destroy()
        if ruta:
            st.session_state[clave_estado] = ruta
    except Exception as error:
        st.error(f"No se pudo abrir el explorador de archivos: {error}")


st.session_state.setdefault("ruta_autorizacion", "")
st.session_state.setdefault("ruta_cedulas", "")
st.session_state.setdefault("df_preparado", None)
st.session_state.setdefault("ruta_salida_csv", "")

col1, col2 = st.columns(2)

with col1:
    st.text_input("PDF de autorización (AUT_CONS_ANTEC.pdf)", key="ruta_autorizacion")
    st.button(
        "Buscar archivo de autorización...",
        on_click=_elegir_archivo,
        args=("ruta_autorizacion", "Selecciona el PDF de autorización", [("PDF", "*.pdf")]),
    )

with col2:
    st.text_input("PDF de copias de cédula (opcional)", key="ruta_cedulas")
    st.button(
        "Buscar archivo de cédulas...",
        on_click=_elegir_archivo,
        args=("ruta_cedulas", "Selecciona el PDF de copias de cédula", [("PDF", "*.pdf")]),
    )

st.divider()

if st.button("Leer y conciliar", type="primary"):
    ruta_autorizacion = st.session_state["ruta_autorizacion"].strip()
    ruta_cedulas = st.session_state["ruta_cedulas"].strip()

    if not ruta_autorizacion or not os.path.isfile(ruta_autorizacion):
        st.error("No se encontró el PDF de autorización. Verifica la ruta.")
    else:
        with st.spinner("Leyendo el PDF de autorización..."):
            df = pp.leer_autorizaciones(ruta_autorizacion)

        if df.empty:
            st.error(
                "No se encontró ninguna persona en el PDF de autorización. "
                "Puede que use una redacción distinta a las ya conocidas — avísame para revisarla."
            )
        else:
            fechas_cedulas = {}
            if ruta_cedulas and os.path.isfile(ruta_cedulas):
                with st.spinner("Leyendo el PDF de cédulas (usa OCR si hace falta, puede tardar)..."):
                    fechas_cedulas = pp.leer_fechas_desde_cedulas(ruta_cedulas, set(df["DOC"]))

            df_final = pp.conciliar(df, fechas_cedulas)
            st.session_state["df_preparado"] = df_final
            st.session_state["ruta_salida_csv"] = os.path.join(
                os.path.dirname(ruta_autorizacion), "personas_preparadas.csv"
            )
            st.success(f"Se encontraron {len(df_final)} persona(s).")

df_actual = st.session_state.get("df_preparado")

if df_actual is not None:
    total_revisar = int((df_actual["REVISAR"] == "SI").sum())
    if total_revisar:
        st.warning(
            f"{total_revisar} persona(s) quedaron marcadas para revisar (columna REVISAR = SI). "
            "Corrígelas directamente en la tabla antes de guardar."
        )
    else:
        st.success("Todas las personas coincidieron entre las dos fuentes. No hay nada que revisar.")

    st.markdown("**Revisa y corrige lo que haga falta directamente en la tabla:**")
    df_editado = st.data_editor(
        df_actual[COLUMNAS_EDITABLES],
        num_rows="fixed",
        use_container_width=True,
        key="editor_personas",
    )

    if st.button("Guardar CSV", type="primary"):
        ruta_salida = st.session_state["ruta_salida_csv"]
        directorio_base = os.path.dirname(ruta_salida)

        if os.path.isfile(ruta_salida):
            from datetime import datetime

            import shutil

            marca_tiempo = datetime.now().strftime("%Y%m%d_%H%M%S")
            ruta_respaldo = os.path.join(directorio_base, f"personas_preparadas.bak_{marca_tiempo}.csv")
            shutil.copy2(ruta_salida, ruta_respaldo)
            st.info(f"Ya existía un CSV en esa carpeta: se guardó una copia de respaldo antes de reemplazarlo.")

        df_editado.to_csv(ruta_salida, index=False, encoding="utf-8-sig")
        st.success(f"Guardado en:\n{ruta_salida}")
        st.caption("Ya puedes ir a la página **2. Ejecutar verificaciones** y seleccionar este CSV.")
