"""
Interfaz web local para la revisión técnico-administrativa.

Es una capa visual sobre los mismos scripts de siempre (preparar_personas.py
y los 5 automation_*.py) — no reemplaza la lógica, solo evita tener que usar
la consola y Excel para revisar los datos antes de consultar.

Se corre con:
    streamlit run webapp/Inicio.py
"""
import streamlit as st

st.set_page_config(page_title="Revisión Técnico-Administrativa", page_icon="📋", layout="wide")

st.title("Revisión técnico-administrativa")
st.caption("Alcaldía de Cali — Estímulos y convocatorias")

st.markdown(
    """
Esta aplicación te guía por los mismos dos pasos que ya conoces, pero sin
consola ni Excel:

1. **Preparar personas** — le das el PDF de autorización (y, si lo tienes, el de
   copias de cédula) de un postulante o grupo. La app extrae los datos, cruza
   las fechas de expedición contra la cédula (usando OCR si la cédula es una
   foto/escaneo) y te deja **revisar y corregir directamente en la pantalla**
   antes de guardar el CSV.
2. **Ejecutar verificaciones** — eliges el CSV ya revisado y qué portales
   correr (RNMC, Contraloría, Procuraduría, Judicial, Delitos Sexuales). Cada
   uno abre su propia ventana de Chrome, igual que siempre — la app solo te
   muestra el progreso y el resumen en un solo lugar.

Usa el menú de la izquierda para moverte entre los pasos.

---

**Importante:** esta app corre en tu computador, no en la nube. Los PDF, las
cédulas y los certificados nunca salen de tu equipo — la app solo llama a los
mismos scripts que ya usabas.
"""
)
