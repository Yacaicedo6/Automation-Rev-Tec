#!/bin/bash
# Levanta todo lo necesario para usar el panel: un "cupo" completo (pantalla
# virtual + VNC + visor web) por cada sesión simultánea que permita
# config.env, y el propio panel.
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

# Lee MAX_SESIONES_PARALELAS de config.env (sin pisar otras variables del
# entorno que ya existan con ese nombre).
MAX_SESIONES_PARALELAS=$(grep -m1 '^MAX_SESIONES_PARALELAS=' "$DIR_PANEL/config.env" | cut -d= -f2)
MAX_SESIONES_PARALELAS=${MAX_SESIONES_PARALELAS:-2}
echo "Cupos simultáneos configurados: $MAX_SESIONES_PARALELAS (panel/config.env)"

echo "Deteniendo procesos anteriores (si los hay)..."
pkill Xvfb 2>/dev/null
pkill fluxbox 2>/dev/null
pkill x11vnc 2>/dev/null
pkill websockify 2>/dev/null
pkill -f uvicorn 2>/dev/null
sleep 1

sudo mkdir -p /tmp/.X11-unix 2>/dev/null
sudo chmod 1777 /tmp/.X11-unix 2>/dev/null

for i in $(seq 1 "$MAX_SESIONES_PARALELAS"); do
    VNC_PORT=$((5900 + i))
    WS_PORT=$((6079 + i))

    echo "Cupo $i: pantalla :$i, VNC puerto $VNC_PORT, visor web puerto $WS_PORT..."

    export DISPLAY=":$i"
    Xvfb ":$i" -screen 0 1280x800x24 > /dev/null 2>&1 &
    sleep 0.5
    fluxbox > /dev/null 2>&1 &
    sleep 0.5
    unset WAYLAND_DISPLAY
    x11vnc -display ":$i" -nopw -shared -forever -rfbport "$VNC_PORT" > /dev/null 2>&1 &
    sleep 0.5
    websockify --web=/usr/share/novnc/ "$WS_PORT" "localhost:$VNC_PORT" > /dev/null 2>&1 &
    sleep 0.5
done
unset DISPLAY

echo "Iniciando el panel..."
cd "$DIR_PANEL"
uvicorn backend.main:app --host 0.0.0.0 --port 8600 &
cd "$DIR_PROYECTO"
sleep 1

echo ""
echo "===================================================="
echo "Listo. $MAX_SESIONES_PARALELAS cupo(s) de \"Ejecutar verificaciones\" disponibles."
echo ""
echo "Para ti, en este mismo equipo:  http://localhost:8600"
echo ""
echo "Para que tus compañeros entren desde la red, todavía hace falta un"
echo "paso en Windows (una sola vez por reinicio de WSL): abre PowerShell"
echo "COMO ADMINISTRADOR y corre:"
echo "    panel\\configurar_red.ps1"
echo ""
echo "Deja esta ventana de terminal abierta mientras se use el panel."
echo "Para apagar todo: source panel/detener.sh"
echo "===================================================="
