"""
Página 3: panorama general — un vistazo del estado de todos los postulantes
o grupos dentro de una carpeta raíz de convocatoria, sin tener que abrir
carpeta por carpeta en el Explorador.
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

from _estilos import aplicar_estilos, insignia_html  # noqa: E402

st.set_page_config(page_title="Panorama general", page_icon="🗂️", layout="wide")
aplicar_estilos()
st.title("🗂️ 3. Panorama general")

ENTIDADES = [
    ("RNMC", "RNMC"),
    ("CONT", "Contraloría"),
    ("PROC", "Procuraduría"),
    ("JUD", "Judicial"),
    ("DSEX", "Delitos sexuales"),
]


def _elegir_carpeta():
    try:
        import tkinter as tk
        from tkinter import filedialog

        raiz = tk.Tk()
        raiz.attributes("-topmost", True)
        raiz.withdraw()
        ruta = filedialog.askdirectory(title="Selecciona la carpeta raíz de la convocatoria")
        raiz.destroy()
        if ruta:
            st.session_state["carpeta_raiz"] = ruta
    except Exception as error:
        st.error(f"No se pudo abrir el explorador de carpetas: {error}")


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


def _resumen_entidad(carpeta_grupo, codigo, documentos):
    estados = [_estado_persona(carpeta_grupo, codigo, doc) for doc in documentos]
    if any(e == "alerta" for e in estados):
        return insignia_html(f"⚠️ {estados.count('alerta')} con alerta", "alerta")
    ok = estados.count("ok")
    total = len(estados)
    if ok == total:
        return insignia_html(f"✅ {ok}/{total}", "ok")
    return insignia_html(f"⏳ {ok}/{total}", "pendiente")


st.session_state.setdefault("carpeta_raiz", "")

st.text_input("Carpeta raíz de la convocatoria (donde están las carpetas de cada postulante)", key="carpeta_raiz")
st.button("Buscar carpeta...", on_click=_elegir_carpeta)

if st.button("Actualizar panorama", type="primary"):
    st.session_state["panorama_actualizado"] = True

carpeta_raiz = st.session_state["carpeta_raiz"].strip()

if carpeta_raiz and st.session_state.get("panorama_actualizado"):
    grupos = _buscar_grupos(carpeta_raiz)

    if not grupos:
        st.warning("No se encontró ningún PDF de autorización (AUT_CONS_ANTEC.pdf) dentro de esa carpeta.")
    else:
        st.caption(f"{len(grupos)} postulante(s)/grupo(s) encontrados.")

        for carpeta_grupo in grupos:
            ruta_csv = carpeta_grupo / "personas_preparadas.csv"
            nombre_grupo = carpeta_grupo.name

            with st.container(border=True):
                col_nombre, col_boton = st.columns([5, 1])
                col_nombre.markdown(f"**{nombre_grupo}**")
                if col_boton.button("Abrir carpeta", key=f"abrir_{carpeta_grupo}"):
                    os.startfile(str(carpeta_grupo))

                if not ruta_csv.is_file():
                    st.caption("Sin preparar todavía (falta correr *Preparar personas*).")
                    continue

                try:
                    df_grupo = pd.read_csv(ruta_csv, dtype={"DOC": str})
                except Exception as error:
                    st.caption(f"No se pudo leer personas_preparadas.csv: {error}")
                    continue

                documentos = list(df_grupo["DOC"])
                pendientes_revision = int((df_grupo["REVISAR"] == "SI").sum()) if "REVISAR" in df_grupo.columns else 0

                cols = st.columns(len(ENTIDADES) + 1)
                cols[0].caption(f"{len(documentos)} persona(s)")
                if pendientes_revision:
                    cols[0].markdown(
                        insignia_html(f"{pendientes_revision} por revisar en el CSV", "alerta"),
                        unsafe_allow_html=True,
                    )

                for col, (codigo, etiqueta) in zip(cols[1:], ENTIDADES):
                    col.caption(etiqueta)
                    col.markdown(_resumen_entidad(carpeta_grupo, codigo, documentos), unsafe_allow_html=True)
else:
    st.info("Indica la carpeta raíz y presiona **Actualizar panorama** para ver el estado de todos los postulantes.")
