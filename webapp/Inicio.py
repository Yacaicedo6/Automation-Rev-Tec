"""
Interfaz web local para la revisión técnico-administrativa.

Es una capa visual sobre los mismos scripts de siempre (preparar_personas.py
y los 5 automation_*.py) — no reemplaza la lógica, solo evita tener que usar
la consola y Excel para revisar los datos antes de consultar.

Se corre con:
    streamlit run webapp/Inicio.py
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _estilos import aplicar_estilos  # noqa: E402

st.set_page_config(page_title="Revisión Técnico-Administrativa", page_icon="📋", layout="wide")
aplicar_estilos()

st.title("📋 Revisión técnico-administrativa")
st.caption("Alcaldía de Cali — Estímulos y convocatorias")

st.markdown(
    """
    <div class="tarjeta-paso">
        <h3>1️⃣ Preparar personas</h3>
        Le das el PDF de autorización (y, si lo tienes, el de copias de cédula)
        de un postulante o grupo. La app extrae los datos, cruza las fechas de
        expedición contra la cédula (usando OCR si es una foto/escaneo) y te
        deja <b>revisar y corregir directamente en la pantalla</b> antes de
        guardar el CSV.
    </div>
    <div class="tarjeta-paso">
        <h3>2️⃣ Ejecutar verificaciones</h3>
        Eliges el CSV ya revisado y qué portales correr (RNMC, Contraloría,
        Procuraduría, Judicial, Delitos Sexuales). Cada uno abre su propia
        ventana de Chrome, igual que siempre — la app solo te muestra el
        progreso y el resumen en un solo lugar.
    </div>
    <div class="tarjeta-paso">
        <h3>3️⃣ Panorama general</h3>
        Un vistazo de todos los postulantes o grupos de una convocatoria: cuántas
        personas tiene cada uno y en qué va cada verificación, sin abrir
        carpeta por carpeta en el Explorador.
    </div>
    """,
    unsafe_allow_html=True,
)

st.divider()
st.info(
    "👈 Usa el menú de la izquierda para moverte entre los pasos.\n\n"
    "**Importante:** esta app corre en tu computador, no en la nube. Los PDF, las "
    "cédulas y los certificados nunca salen de tu equipo — la app solo llama a los "
    "mismos scripts que ya usabas."
)
