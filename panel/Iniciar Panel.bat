@echo off
REM Doble clic para levantar el panel completo:
REM  1) Abre WSL en la carpeta del proyecto y corre panel/iniciar.sh, en una
REM     ventana aparte que hay que dejar abierta mientras se use el panel.
REM  2) Dispara panel\configurar_red.ps1 elevado, para que tus companeros
REM     puedan entrar desde la red sin que tengas que abrir PowerShell como
REM     administrador a mano cada vez. Windows va a pedir tu confirmacion
REM     (UAC) una sola vez -- eso no se puede evitar, es una proteccion de
REM     Windows para cualquier cosa que toque el firewall.

set "PROJDIR=%~dp0.."
for /f "delims=" %%i in ('wsl.exe wslpath -a "%PROJDIR%"') do set "WSLPATH=%%i"

if "%WSLPATH%"=="" (
    echo No se pudo encontrar WSL. ^Esta instalado?
    pause
    exit /b 1
)

start "Panel Rev-Tec" wsl.exe --cd "%WSLPATH%" -- bash -ic "source panel/iniciar.sh; exec bash"

echo Esperando a que WSL arranque...
timeout /t 8 /nobreak >nul

echo Configurando el acceso desde la red (Windows va a pedir tu confirmacion)...
powershell -NoProfile -Command "Start-Process -FilePath 'powershell' -ArgumentList '-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File','%~dp0configurar_red.ps1' -Verb RunAs"
