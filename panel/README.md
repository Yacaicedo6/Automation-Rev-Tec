# Panel web de la revisión técnico-administrativa

Interfaz web (FastAPI + Jinja2) sobre los mismos scripts de siempre
(`preparar_personas.py` y los 5 `automation_*.py`). A diferencia de la
webapp de Streamlit (`../webapp`), este panel corre **dentro de WSL** para
poder mostrar el navegador de las verificaciones **en vivo, embebido en la
misma página**, vía VNC — así se puede resolver una pregunta de seguridad
de Procuraduría o corregir un error de Judicial/Delitos Sexuales sin tener
que estar mirando una ventana de Chrome aparte.

No reemplaza nada de la lógica de negocio: sigue siendo el mismo código de
siempre, solo que servido distinto.

## Requisitos (primera vez)

1. **WSL2 con Ubuntu.** Si no lo tienes:
   ```powershell
   wsl --install -d Ubuntu-24.04
   ```
   Si el disco de tu PC anda corto de espacio, reubica el disco virtual de
   WSL a otra unidad después de instalarlo (ver el historial de esta
   conversación para el procedimiento exacto de exportar/reimportar).

2. **Paquetes del sistema dentro de Ubuntu** (pantalla virtual, VNC, visor
   web, gestor de ventanas):
   ```bash
   sudo apt update
   sudo apt install -y xvfb x11vnc novnc websockify x11-apps fluxbox python3-venv python3-pip
   ```

3. **Google Chrome** (Selenium lo necesita real, no el Chromium de Ubuntu,
   que en WSL suele venir roto vía snap):
   ```bash
   cd ~
   wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
   sudo apt install -y ./google-chrome-stable_current_amd64.deb
   ```

4. **Entorno virtual de Python y dependencias** (desde la carpeta de este
   proyecto dentro de WSL, ej. `/mnt/e/.../Automation-Rev-Tec`):
   ```bash
   python3 -m venv ~/venv-rev-tec
   source ~/venv-rev-tec/bin/activate
   pip install -r panel/requirements-wsl.txt
   ```
   Este paso puede tardar varios minutos la primera vez (EasyOCR trae
   dependencias pesadas). El `requirements-wsl.txt` ya está armado para
   instalar la versión de PyTorch **solo-CPU** (mucho más liviana) — no lo
   cambies por `pip install torch` a secas, porque eso trae soporte CUDA
   completo (varios GB innecesarios) y además puede quedar en una versión
   incompatible con `torchvision`.

## Uso diario

Desde una terminal de WSL, parado en la carpeta del proyecto:

```bash
source panel/iniciar.sh
```

**Importante: con `source`, no ejecutándolo directo** (`bash panel/iniciar.sh`
NO sirve) — si no, los procesos de fondo (Xvfb, VNC, el panel) quedan atados
a la sesión del script y mueren en cuanto termina.

Cuando termine, abre **http://localhost:8600** en tu navegador. Deja esa
ventana de terminal abierta mientras uses el panel.

Para apagar todo:
```bash
source panel/detener.sh
```

## Si abres una ventana de WSL nueva

Cada ventana de WSL tiene su propia sesión — lo que corriste en una no se
comparte con otra. Si abres una ventana nueva y necesitas el panel ahí,
vuelve a correr `source panel/iniciar.sh` en esa ventana.

## Problemas conocidos

- **"Failed to connect to server" en el visor de VNC:** casi siempre es que
  `x11vnc` o `Xvfb` se cayeron (se cierran si cierras la terminal donde los
  iniciaste). Corre `source panel/iniciar.sh` de nuevo.
- **`XOpenDisplay(":1") failed`:** la pantalla virtual (Xvfb) ya no existe,
  no solo el VNC. También lo arregla `source panel/iniciar.sh`.
- **`Command 'uvicorn' not found`:** se te olvidó activar el entorno
  (`source ~/venv-rev-tec/bin/activate`) o estás en una ventana de WSL
  donde nunca lo instalaste.
- **Error `operator torchvision::nms does not exist`:** `torch` y
  `torchvision` quedaron de builds distintas. Reinstala ambos desde
  `panel/requirements-wsl.txt` y **reinicia el panel** (los procesos ya
  corriendo no se enteran de un `pip install` nuevo).

## Seguridad

Este panel **no tiene inicio de sesión** y el VNC corre **sin contraseña**
(`-nopw`). Eso está bien mientras todo se quede en `localhost` de tu propio
equipo. **No lo expongas a internet ni a la red local** (ni con ngrok, ni
con Tailscale, ni abriendo puertos) sin antes agregar autenticación real —
esta herramienta maneja cédulas y datos personales de postulantes.
