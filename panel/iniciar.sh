#!/bin/bash
# Levanta todo lo necesario para usar el panel: pantalla virtual, VNC, el
# visor web de VNC, y el propio panel.
#
# IMPORTANTE: correr con "source", no ejecutando el archivo directamente --
# si no, los procesos quedan atados a un proceso hijo que muere en cuanto
# este script termina, y todo se cae solo (nos pasó varias veces).
#
#   source panel/iniciar.sh
#
# Si abres una ventana de WSL nueva, tienes que volver a correrlo ahí --
# cada ventana tiene su propia sesión y lo que se corrió en otra no se
# comparte.

(return 0 2>/dev/null) && SOURCEADO=1 || SOURCEADO=0
if [ "$SOURCEADO" != "1" ]; then
    echo "ERROR: este script debe correrse con 'source', no ejecutándolo directamente."
    echo "Usa:  source $(basename "${BASH_SOURCE[0]:-$0}")"
    return 1 2>/dev/null || exit 1
fi

DIR_PANEL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR_PROYECTO="$(dirname "$DIR_PANEL")"

echo "Activando entorno virtual..."
source ~/venv-rev-tec/bin/activate

echo "Deteniendo procesos anteriores (si los hay)..."
pkill Xvfb 2>/dev/null
pkill fluxbox 2>/dev/null
pkill x11vnc 2>/dev/null
pkill websockify 2>/dev/null
pkill -f uvicorn 2>/dev/null
sleep 1

sudo mkdir -p /tmp/.X11-unix 2>/dev/null
sudo chmod 1777 /tmp/.X11-unix 2>/dev/null

echo "Iniciando pantalla virtual (Xvfb :1)..."
Xvfb :1 -screen 0 1280x800x24 &
export DISPLAY=:1
sleep 1

echo "Iniciando gestor de ventanas..."
fluxbox &
sleep 1

echo "Iniciando servidor VNC..."
unset WAYLAND_DISPLAY
x11vnc -display :1 -nopw -shared -forever &
sleep 1

echo "Iniciando visor web de VNC (noVNC)..."
websockify --web=/usr/share/novnc/ 6080 localhost:5900 &
sleep 1

echo "Iniciando el panel..."
cd "$DIR_PANEL"
uvicorn backend.main:app --host 0.0.0.0 --port 8600 &
cd "$DIR_PROYECTO"
sleep 1

echo ""
echo "===================================================="
echo "Listo. Abre http://localhost:8600 en tu navegador."
echo "Deja esta ventana de terminal abierta mientras uses el panel."
echo "Para apagar todo: source panel/detener.sh"
echo "===================================================="
