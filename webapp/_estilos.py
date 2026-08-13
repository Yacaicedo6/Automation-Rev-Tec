"""
Estilos compartidos entre las páginas de la webapp: lienzo crema editorial,
tinta casi negra, un único acento naranja para las acciones principales, e
insignias en pastilla para los estados (listo / pendiente / alerta), para
que se puedan distinguir de un vistazo en vez de leer texto.
"""
import streamlit as st

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
    --color-primary: #F54E00;
    --color-ink: #26251E;
    --color-body: #5A5852;
    --color-muted: #807D72;
    --color-canvas: #F7F7F4;
    --color-canvas-soft: #FAFAF7;
    --color-surface-card: #FFFFFF;
    --color-surface-strong: #E6E5E0;
    --color-hairline: #E6E5E0;
    --color-success: #1F8A65;
    --color-success-bg: #E3F3EC;
    --color-error: #CF2D56;
    --color-error-bg: #FBE3E9;
    --color-warning: #96650A;
    --color-warning-bg: #F7ECD9;
}

html, body, .stApp {
    font-family: 'Inter', system-ui, 'Helvetica Neue', Helvetica, Arial, sans-serif !important;
    color: var(--color-ink);
    letter-spacing: -0.01em;
}

code, pre {
    font-family: 'JetBrains Mono', 'Fira Code', monospace !important;
}

/* Encabezados: voz editorial, peso regular y tracking negativo en vez de
   negrita, salvo los títulos de componentes (tarjetas), que se ven mejor
   con un poco más de peso para escanear rápido. */
h1, h2 {
    font-weight: 400;
    letter-spacing: -0.02em;
    color: var(--color-ink);
}
h3 {
    font-weight: 600;
    letter-spacing: -0.005em;
    color: var(--color-ink);
}

/* Tarjetas de la página de inicio: solo hairline, sin sombra ni degradado */
.tarjeta-paso {
    background: var(--color-surface-card);
    border: 1px solid var(--color-hairline);
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1rem;
}
.tarjeta-paso h3 {
    margin-top: 0;
    display: flex;
    align-items: center;
    gap: 0.6rem;
    color: var(--color-ink);
}
.tarjeta-paso p, .tarjeta-paso {
    color: var(--color-body);
}

/* Numeral de paso: reemplaza los emojis de número por una insignia propia,
   consistente con el resto del sistema (tipografía + color, sin íconos). */
.paso-numero {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.75rem;
    height: 1.75rem;
    flex-shrink: 0;
    border-radius: 8px;
    background-color: var(--color-surface-strong);
    color: var(--color-ink);
    font-size: 0.95rem;
    font-weight: 600;
}

/* Insignias de estado, en pastilla */
.insignia {
    display: inline-block;
    padding: 0.2rem 0.7rem;
    border-radius: 9999px;
    font-weight: 600;
    font-size: 0.72rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    white-space: nowrap;
}
.insignia-ok {
    background-color: var(--color-success-bg);
    color: var(--color-success);
}
.insignia-pendiente {
    background-color: var(--color-warning-bg);
    color: var(--color-warning);
}
.insignia-alerta {
    background-color: var(--color-error-bg);
    color: var(--color-error);
}
.insignia-neutro {
    background-color: var(--color-surface-strong);
    color: var(--color-ink);
}

/* Sidebar: lienzo crema, igual que el resto de la app */
section[data-testid="stSidebar"] {
    background-color: var(--color-canvas);
    border-right: 1px solid var(--color-hairline);
}
</style>
"""


def aplicar_estilos():
    st.markdown(_CSS, unsafe_allow_html=True)


def insignia_html(texto, tipo="neutro"):
    """tipo: 'ok', 'pendiente', 'alerta' o 'neutro'."""
    return f'<span class="insignia insignia-{tipo}">{texto}</span>'
