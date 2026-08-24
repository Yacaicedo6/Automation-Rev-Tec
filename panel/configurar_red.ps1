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

# Rango de direcciones de la red de ZeroTier "REV_TEC_ADMIN"
# (id 08752e18b1177ed7). Si algún día se crea una red nueva de ZeroTier,
# hay que actualizar este valor con el nuevo rango (se ve en
# my.zerotier.com, en la sección "IPv4 Assignment" de la red).
$subredZeroTier = "10.22.202.0/24"

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

    # Regla aparte para Tailscale: solo deja pasar tráfico que venga
    # realmente de la red privada de Tailscale (su rango de direcciones
    # 100.64.0.0/10), sin importar cómo Windows clasifique esa red
    # (normalmente la marca como "pública", y no queremos abrir esa
    # categoría en general -- solo el tráfico que sí es de Tailscale).
    $nombreReglaTailscale = "Panel Rev-Tec Tailscale - puerto $puerto"
    Remove-NetFirewallRule -DisplayName $nombreReglaTailscale -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $nombreReglaTailscale -Direction Inbound -Protocol TCP -LocalPort $puerto -RemoteAddress "100.64.0.0/10" -Action Allow -Profile Any | Out-Null

    # Lo mismo, pero para la red de ZeroTier.
    $nombreReglaZeroTier = "Panel Rev-Tec ZeroTier - puerto $puerto"
    Remove-NetFirewallRule -DisplayName $nombreReglaZeroTier -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $nombreReglaZeroTier -Direction Inbound -Protocol TCP -LocalPort $puerto -RemoteAddress $subredZeroTier -Action Allow -Profile Any | Out-Null
}
Write-Host "Puertos abiertos hacia WSL ($ipWSL): $($puertos -join ', ')"

$ipWindows = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch "Loopback|vEthernet|WSL|ZeroTier|Tailscale" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1 -ExpandProperty IPAddress)

$ipTailscale = $null
$rutaTailscale = "C:\Program Files\Tailscale\tailscale.exe"
try {
    if (Get-Command tailscale -ErrorAction SilentlyContinue) {
        $ipTailscale = (& tailscale ip -4 2>$null | Select-Object -First 1)
    } elseif (Test-Path $rutaTailscale) {
        $ipTailscale = (& $rutaTailscale ip -4 2>$null | Select-Object -First 1)
    }
} catch {
    $ipTailscale = $null
}

$ipZeroTier = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "*ZeroTier*" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty IPAddress)

$lineasResumen = @()
$lineasResumen += ""
$lineasResumen += "===================================================="
$lineasResumen += "Listo. Tus compañeros, desde la misma red, ya pueden entrar a:"
$lineasResumen += "    http://${ipWindows}:8600"
$lineasResumen += ""
if ($ipTailscale) {
    $lineasResumen += "Y desde fuera de la oficina, con Tailscale instalado y conectado"
    $lineasResumen += "a esta misma tailnet, pueden entrar a:"
    $lineasResumen += "    http://${ipTailscale}:8600"
    $lineasResumen += ""
}
if ($ipZeroTier) {
    $lineasResumen += "Y desde fuera de la oficina, con ZeroTier instalado y unido a"
    $lineasResumen += "la red REV_TEC_ADMIN, pueden entrar a:"
    $lineasResumen += "    http://${ipZeroTier}:8600"
    $lineasResumen += ""
}
$lineasResumen += "Nota: si no entran desde la red de la oficina, revisa que esa red esté"
$lineasResumen += "marcada como 'Privada' (no 'Pública') en Configuración de Windows."
$lineasResumen += "Tailscale y ZeroTier no dependen de este ajuste."
$lineasResumen += "===================================================="

$lineasResumen | ForEach-Object { Write-Host $_ }

# Se deja este resumen en un archivo (dentro de panel/, que WSL también ve)
# para que iniciar.sh lo recoja y lo muestre en la ventana del panel -- así
# la persona no tiene que quedarse revisando dos ventanas distintas.
$rutaInfoRed = Join-Path $PSScriptRoot ".info_red.txt"
[System.IO.File]::WriteAllText($rutaInfoRed, ($lineasResumen -join "`n") + "`n", [System.Text.UTF8Encoding]::new($false))
