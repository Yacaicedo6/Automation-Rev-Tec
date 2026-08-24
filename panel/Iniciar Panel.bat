@echo off
REM Doble clic para levantar el panel completo:
REM  1) Se relanza a si mismo como administrador si hace falta (Windows pide
REM     confirmar una sola vez con UAC al principio -- no se puede evitar
REM     ese aviso, es una proteccion de Windows para lo que sigue).
REM  2) Abre WSL en la carpeta del proyecto y corre panel/iniciar.sh, en una
REM     ventana aparte que hay que dejar abierta mientras se use el panel.
REM  3) Corre panel\configurar_red.ps1 (esta ventana ya viene elevada desde
REM     el paso 1, asi que no vuelve a pedir confirmacion) para que tus
REM     companeros puedan entrar desde la red. wsl.exe espera por su cuenta
REM     a que la maquina virtual este lista, asi que no hace falta simular
REM     esa espera aqui con un temporizador fijo. El resumen con las URLs
REM     queda en la ventana del panel (configurar_red.ps1 lo deja en un
REM     archivo que iniciar.sh recoge), asi que esta ventana se cierra sola.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Se necesita permiso de administrador para configurar el acceso desde la red...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
    exit /b
)

set "PROJDIR=%~dp0.."
for /f "delims=" %%i in ('wsl.exe wslpath -a "%PROJDIR%"') do set "WSLPATH=%%i"

if "%WSLPATH%"=="" (
    echo No se pudo encontrar WSL. ^Esta instalado?
    pause
    exit /b 1
)

start "Panel Rev-Tec" wsl.exe --cd "%WSLPATH%" -- bash -ic "source panel/iniciar.sh; exec bash"

echo Configurando el acceso desde la red...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configurar_red.ps1"
