@echo off
REM Doble clic para levantar el panel: abre WSL en la carpeta del proyecto
REM y corre panel/iniciar.sh ahi mismo, dejando la ventana abierta.
REM
REM No reemplaza el paso de red la primera vez: sigue haciendo falta correr
REM "Configurar Red.bat" (o panel\configurar_red.ps1 como administrador)
REM despues de este, para que tus companeros puedan entrar desde la red.

set "PROJDIR=%~dp0.."
for /f "delims=" %%i in ('wsl.exe wslpath -a "%PROJDIR%"') do set "WSLPATH=%%i"

if "%WSLPATH%"=="" (
    echo No se pudo encontrar WSL. ^Esta instalado?
    pause
    exit /b 1
)

wsl.exe --cd "%WSLPATH%" -- bash -ic "source panel/iniciar.sh; exec bash"
