"""
Estilos compartidos entre las páginas de la webapp: paleta pastel con
acentos vivos, e insignias de color para los estados (listo / pendiente /
alerta), para que se puedan distinguir de un vistazo en vez de leer texto.
"""
import streamlit as st

_CSS = """
<style>
/* Tarjetas de la página de inicio */
.tarjeta-paso {
    background: linear-gradient(135deg, #F3F0FF 0%, #EAFBF3 100%);
    border: 1px solid #E4DDFF;
    border-radius: 18px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
}
.tarjeta-paso h3 {
    margin-top: 0;
    color: #4B3FCC;
}

/* Insignias de estado */
.insignia {
    display: inline-block;
    padding: 0.15rem 0.75rem;
    border-radius: 999px;
    font-weight: 600;
    font-size: 0.85rem;
    white-space: nowrap;
}
.insignia-ok {
    background-color: #D7F7E4;
    color: #157347;
}
.insignia-pendiente {
    background-color: #FFF1D6;
    color: #B5750B;
}
.insignia-alerta {
    background-color: #FFE1E8;
    color: #C0233C;
}
.insignia-neutro {
    background-color: #EDEAFB;
    color: #5B5591;
}

/* Encabezados de sección con un poco más de aire */
h1, h2, h3 {
    letter-spacing: -0.01em;
}
</style>
"""


def aplicar_estilos():
    st.markdown(_CSS, unsafe_allow_html=True)


def insignia_html(texto, tipo="neutro"):
    """tipo: 'ok', 'pendiente', 'alerta' o 'neutro'."""
    return f'<span class="insignia insignia-{tipo}">{texto}</span>'
