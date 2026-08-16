# Deja que tus compañeros, desde otras computadoras de la misma red, puedan
# entrar al panel que corre dentro de WSL. Por defecto, WSL2 solo reenvía
# "localhost" para el propio Windows -- el tráfico que llega de otra
# computadora en la red no entra a WSL sin esto.
#
# Hay que correrlo COMO ADMINISTRADOR cada vez que reinicies WSL (o el
# equipo), porque WSL2 le asigna una IP interna nueva cada vez que arranca.
#
# Uso: clic derecho > "Ejecutar con PowerShell" (como administrador), o
# desde una PowerShell de administrador:
#     panel\configurar_red.ps1

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Host "Este script necesita correr como Administrador. Ábrelo con clic derecho > 'Ejecutar como administrador'." -ForegroundColor Red
    exit 1
}

# Lee el número de cupos desde config.env, igual que iniciar.sh, para saber
# cuántos puertos de VNC hay que abrir además del puerto del panel.
$rutaConfig = Join-Path $PSScriptRoot "config.env"
$maxSesiones = 2
if (Test-Path $rutaConfig) {
    $linea = Get-Content $rutaConfig | Where-Object { $_ -match '^MAX_SESIONES_PARALELAS=' }
    if ($linea) {
        $maxSesiones = [int]($linea -replace 'MAX_SESIONES_PARALELAS=', '').Trim()
    }
}
Write-Host "Cupos configurados: $maxSesiones (panel/config.env)"

# Puertos a exponer: el panel (8600) + un puerto de visor VNC por cada cupo
# (6080, 6081, ... -- deben coincidir con panel/iniciar.sh).
$puertos = @(8600)
for ($i = 1; $i -le $maxSesiones; $i++) {
    $puertos += (6079 + $i)
}

$ipWSL = (wsl hostname -I).Trim().Split(" ")[0]
if (-not $ipWSL) {
    Write-Host "No se pudo obtener la IP de WSL. ¿Ya corriste 'source panel/iniciar.sh' dentro de WSL?" -ForegroundColor Red
    exit 1
}
Write-Host "IP interna de WSL ahora mismo: $ipWSL"

foreach ($puerto in $puertos) {
    # Se borra la regla anterior si existía (la IP de WSL cambia en cada
    # reinicio), y se crea de nuevo apuntando a la IP actual.
    netsh interface portproxy delete v4tov4 listenport=$puerto listenaddress=0.0.0.0 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenport=$puerto listenaddress=0.0.0.0 connectport=$puerto connectaddress=$ipWSL | Out-Null

    $nombreRegla = "Panel Rev-Tec - puerto $puerto"
    Remove-NetFirewallRule -DisplayName $nombreRegla -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $nombreRegla -Direction Inbound -Protocol TCP -LocalPort $puerto -Action Allow -Profile Private | Out-Null

    Write-Host "  Puerto $puerto -> WSL ($ipWSL)"
}

$ipWindows = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch "Loopback|vEthernet|WSL" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1 -ExpandProperty IPAddress)

Write-Host ""
Write-Host "===================================================="
Write-Host "Listo. Tus compañeros, desde la misma red, ya pueden entrar a:"
Write-Host "    http://${ipWindows}:8600"
Write-Host ""
Write-Host "Nota: las reglas de firewall que se crearon solo permiten redes"
Write-Host "'privadas' (perfil Private) -- si tu red de Windows está marcada"
Write-Host "como 'pública', cámbiala a privada en Configuración de Windows"
Write-Host "para que esto funcione."
Write-Host "===================================================="
