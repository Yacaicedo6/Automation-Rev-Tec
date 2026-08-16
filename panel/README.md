# Panel web de la revisión técnico-administrativa

Interfaz web (FastAPI + Jinja2) sobre los mismos scripts de siempre
(`preparar_personas.py` y los 5 `automation_*.py`). Corre dentro de WSL para
poder mostrar el navegador de cada verificación **en vivo, embebido en la
misma página**, vía VNC — así se puede resolver una pregunta de seguridad
de Procuraduría o corregir un error de Judicial/Delitos Sexuales sin tener
que estar mirando una ventana de Chrome aparte.

Pensado para que **varios compañeros lo usen a la vez** desde la red local:
cada quien entra con su propio usuario, y puede preparar personas o ver el
panorama general sin límite. Para "Ejecutar verificaciones" (lo único que
necesita Chrome) hay un número **limitado de cupos simultáneos** — si están
todos ocupados, la siguiente persona espera en fila en vez de arriesgar que
el servidor se quede sin memoria.

No reemplaza nada de la lógica de negocio: sigue siendo el mismo código de
siempre, solo que servido distinto.

## Requisitos (primera vez)

1. **WSL2 con Ubuntu.**
   ```powershell
   wsl --install -d Ubuntu-24.04
   ```
   Si el disco de tu PC anda corto de espacio, reubica el disco virtual de
   WSL a otra unidad después de instalarlo.

2. **Paquetes del sistema dentro de Ubuntu:**
   ```bash
   sudo apt update
   sudo apt install -y xvfb x11vnc novnc websockify x11-apps fluxbox python3-venv python3-pip
   ```

3. **Google Chrome** (Selenium lo necesita real, no el Chromium de Ubuntu):
   ```bash
   cd ~
   wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
   sudo apt install -y ./google-chrome-stable_current_amd64.deb
   ```

4. **Entorno virtual y dependencias:**
   ```bash
   python3 -m venv ~/venv-rev-tec
   source ~/venv-rev-tec/bin/activate
   pip install -r panel/requirements-wsl.txt
   ```
   El `requirements-wsl.txt` instala la versión de PyTorch **solo-CPU** a
   propósito (mucho más liviana) — no lo cambies por `pip install torch` a
   secas.

5. **Crea las cuentas de tu equipo** (desde `panel/`, con el entorno
   activado):
   ```bash
   python3 gestionar_usuarios.py agregar nombre_de_usuario
   ```
   Te pide la contraseña de forma interactiva (no queda en texto plano,
   solo su hash). Repite por cada persona. Otros comandos:
   `listar`, `quitar <usuario>`, `cambiar-clave <usuario>`.

## Cuántas sesiones simultáneas soporta

Cada sesión de "Ejecutar verificaciones" necesita su propio Chrome +
pantalla virtual + VNC corriendo al mismo tiempo — eso consume RAM real.
El número de cupos está en **`panel/config.env`**:

```
MAX_SESIONES_PARALELAS=2
```

Súbelo cuando tengas más RAM libre o cambies a un servidor dedicado — tanto
`iniciar.sh` como el backend leen ese mismo número, no hay que tocar
código. Si lo subes, hay que volver a correr `iniciar.sh` (para levantar
los cupos nuevos) y `configurar_red.ps1` (para abrir los puertos nuevos en
el firewall).

## Uso diario

**1. Dentro de WSL**, parado en la carpeta del proyecto:
```bash
source panel/iniciar.sh
```
**Con `source`, no ejecutándolo directo** — si no, los procesos de fondo
mueren en cuanto el script termina.

**2. Para que tus compañeros entren desde la red** (una sola vez por cada
vez que reinicies WSL o el equipo, porque WSL le asigna una IP interna
nueva cada vez): abre PowerShell **como administrador** en Windows y corre:
```powershell
panel\configurar_red.ps1
```
Al final te muestra la dirección que tus compañeros deben usar, algo como
`http://192.168.x.x:8600`. La regla de firewall que crea solo permite redes
marcadas como "privadas" en Windows.

**3. Para apagar todo:**
```bash
source panel/detener.sh
```

## Si abres una ventana de WSL nueva

Cada ventana de WSL tiene su propia sesión — vuelve a correr
`source panel/iniciar.sh` ahí si la necesitas.

## Problemas conocidos

- **"Failed to connect to server" en el visor de VNC:** casi siempre es que
  los procesos de fondo se cayeron (se cierran si cierras la terminal
  donde los iniciaste). Corre `source panel/iniciar.sh` de nuevo.
- **Un compañero no puede entrar desde su computador:** revisa que hayas
  corrido `configurar_red.ps1` **después** del último `iniciar.sh` (la IP
  de WSL cambia en cada reinicio), y que tu red de Windows esté marcada
  como "privada", no "pública".
- **`Command 'uvicorn' not found`:** se te olvidó activar el entorno
  (`source ~/venv-rev-tec/bin/activate`).
- **Error `operator torchvision::nms does not exist`:** `torch` y
  `torchvision` quedaron de builds distintas. Reinstala ambos desde
  `panel/requirements-wsl.txt` y reinicia el panel.

## Seguridad

- Cada persona entra con su propio usuario y contraseña (`gestionar_usuarios.py`).
  Las contraseñas se guardan como hash (bcrypt) en `panel/usuarios.json`,
  que nunca va a git.
- El VNC sigue sin contraseña propia (`-nopw`), pero ya no es alcanzable
  directamente desde fuera: solo se llega a él a través del panel, que sí
  exige haber iniciado sesión.
- Esta herramienta maneja cédulas y datos personales de postulantes.
  Actívala en la red solo dentro de tu red de trabajo (nunca hacia
  internet, ni con ngrok, ni abriendo el router) y solo después de tener
  el visto bueno correspondiente.
