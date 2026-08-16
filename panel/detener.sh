#!/bin/bash
# Apaga todo lo que levanta iniciar.sh (pantalla virtual, VNC, visor web,
# y el panel). Se puede correr con "source" o ejecutándolo directamente,
# a diferencia de iniciar.sh -- aquí no importa porque solo estamos
# matando procesos, no dejando ninguno corriendo de fondo.

echo "Deteniendo el panel y todo lo relacionado con VNC..."
pkill -f uvicorn 2>/dev/null
pkill websockify 2>/dev/null
pkill x11vnc 2>/dev/null
pkill fluxbox 2>/dev/null
pkill Xvfb 2>/dev/null
echo "Listo."
