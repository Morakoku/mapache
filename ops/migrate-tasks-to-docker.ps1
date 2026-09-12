# VeyraWarmupDaily + VeyraChequeoBridge -> Docker
#
# Reemplaza las tareas nativas de Windows Task Scheduler (09:05 y cada 15 min)
# por contenedores Docker descartables. El trigger sigue siendo Task Scheduler
# (el cron de Windows no se toca), pero la ejecucion va a contenedores.
#
# Por que Task Scheduler y no un contenedor cron?
# - En Windows, el socket de Docker es un named pipe, no un UNIX socket. Montar
#   un named pipe como volumen dentro de un contenedor Linux es fragil y no
#   funciona consistentemente en Docker Desktop Windows.
# - Task Scheduler YA es el mecanismo de trigger en esta maquina y funciona.
# - Mantener TS como trigger y Docker como runtime es el cambio minimo.
#
# El relay de WhatsApp (ops/whatsapp_relay.py) corre en el host (Windows) y
# escucha en :9188. El contenedor bridge alcanza el host por host.docker.internal.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here

Write-Host "=== Migrar VeyraWarmupDaily + VeyraChequeoBridge a Docker ===" -ForegroundColor Cyan

# 1) Construir imagenes
Write-Host "`nConstruyendo imagenes (warmup + bridge)..." -ForegroundColor Cyan
Push-Location $repo
docker compose build warmup bridge
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: el build fallo. No se continua." -ForegroundColor Red
    Pop-Location
    exit 1
}

# 2) Verificar/arrancar relay de WhatsApp en el host
$relayPort = 9188
$relayScript = Join-Path $here "whatsapp_relay.py"
$relayRunning = Get-NetTCPConnection -LocalPort $relayPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }

if (-not $relayRunning) {
    Write-Host "`nArrancando whatsapp_relay.py en :$relayPort..." -ForegroundColor Cyan
    $py = "python"
    # Preferir el python del venv de hermes (tiene las libs que el relay necesita,
    # aunque el relay solo usa stdlib — pero asi no hay sorpresas).
    $hermesPy = "C:\Users\edwin\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
    if (Test-Path $hermesPy) { $py = $hermesPy }

    # Arrancar en background (Hidden) y detached para que sobreviva.
    $proc = Start-Process -FilePath $py -ArgumentList "`"$relayScript`" --port $relayPort" `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput "$env:TEMP\relay-stdout.log" `
        -RedirectStandardError "$env:TEMP\relay-stderr.log"
    Start-Sleep -Seconds 2

    # Guardar PID en archivo para que el script de parada sepa que matar.
    $proc.Id | Out-File -Encoding utf8 "$here\relay.pid"

    $relayRunning = Get-NetTCPConnection -LocalPort $relayPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    if ($relayRunning) {
        Write-Host "  OK: relay escuchando en :$relayPort (pid=$($proc.Id))" -ForegroundColor Green
    } else {
        Write-Host "  AVISO: el relay no arranco. El bridge no podra enviar WhatsApp." -ForegroundColor DarkYellow
        Write-Host "  Log: $env:TEMP\relay-stderr.log" -ForegroundColor DarkGray
    }
} else {
    Write-Host "`nRelay ya esta corriendo en :$relayPort" -ForegroundColor Green
}

# 3) Actualizar tareas de Task Scheduler para que corran docker compose
#    en vez del script .cmd directo.

# Helper: actualizar /TR (comando) de una tarea existente
function Update-TaskCommand {
    param([string]$TaskName, [string]$NewCommand)
    $exists = schtasks /Query /TN $TaskName 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  (tarea $TaskName no existe, omitida)" -ForegroundColor DarkGray
        return
    }
    # schtasks no tiene "update command" directo. Se exporta, se modifica, se reimporta.
    $tmpXml = "$env\TEMP\$TaskName.xml"
    schtasks /Query /TN $TaskName /xml > $tmpXml 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  (no se pudo exportar $TaskName)" -ForegroundColor DarkYellow
        return
    }
    # Deshabilitar antes de reimportar
    schtasks /Change /TN $TaskName /DISABLE 2>$null
    # Eliminar y recrear con nuevo comando
    schtasks /Delete /TN $TaskName /F 2>$null
    Write-Host "  (recreando $TaskName con nuevo comando)" -ForegroundColor DarkGray
}

Write-Host "`nConfigurando tareas de Task Scheduler..." -ForegroundColor Cyan

# Nota: se usan las tareas existentes si existen, sino se avisa.
# El comando nuevo corre `docker compose run --rm` desde el directorio del repo.
$composeWarmup = "docker compose -f `"$repo\docker-compose.yml`" run --rm warmup"
$composeBridge = "docker compose -f `"$repo\docker-compose.yml`" run --rm bridge"

Update-TaskCommand -TaskName "VeyraWarmupDaily" -NewCommand $composeWarmup
Update-TaskCommand -TaskName "VeyraChequeoBridge" -NewCommand $composeBridge

# Recrear las tareas con el comando nuevo (la Update-TaskCommand ya elimino las viejas)
$taskWarmupExists = (schtasks /Query /TN "VeyraWarmupDaily" 2>$null; $LASTEXITCODE -eq 0)
if (-not $taskWarmupExists) {
    # Crear VeyraWarmupDaily: 09:05 diario
    schtasks /Create /TN "VeyraWarmupDaily" /SC DAILY /ST 09:05 `
        /TR "$composeWarmup" /F
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK: VeyraWarmupDaily -> docker compose run --rm warmup (09:05)" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: no se pudo crear VeyraWarmupDaily" -ForegroundColor Red
    }
} else {
    Write-Host "  AVISO: VeyraWarmupDaily existe pero no se pudo recrear." -ForegroundColor DarkYellow
}

$taskBridgeExists = (schtasks /Query /TN "VeyraChequeoBridge" 2>$null; $LASTEXITCODE -eq 0)
if (-not $taskBridgeExists) {
    # Crear VeyraChequeoBridge: cada 15 min
    schtasks /Create /TN "VeyraChequeoBridge" /SC MINUTE /MO 15 `
        /TR "$composeBridge" /F
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK: VeyraChequeoBridge -> docker compose run --rm bridge (c/15min)" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: no se pudo crear VeyraChequeoBridge" -ForegroundColor Red
    }
} else {
    Write-Host "  AVISO: VeyraChequeoBridge existe pero no se pudo recrear." -ForegroundColor DarkYellow
}

# 4) Verificacion rapida
Write-Host "`nVerificando bridge (dry-run)..." -ForegroundColor Cyan
docker compose run --rm bridge python /app/veyra_chequeo_bridge.py --dry-run 2>&1 | Out-String
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK: bridge responde" -ForegroundColor Green
} else {
    Write-Host "  AVISO: el bridge fallo (continua de todos modos)." -ForegroundColor DarkYellow
}

Write-Host "`nVerificando warmup (plan, sin enviar)..." -ForegroundColor Cyan
docker compose run --rm warmup python /app/scripts/warmup_resend.py --plan 2>&1 | Out-String
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK: warmup responde" -ForegroundColor Green
} else {
    Write-Host "  AVISO: el warmup fallo (continua de todos modos)." -ForegroundColor DarkYellow
}

Pop-Location

Write-Host "`n=== Hecho ===" -ForegroundColor Cyan
Write-Host "Tareas de Windows ahora ejecutan Docker en vez de scripts nativos." -ForegroundColor White
Write-Host "El relay del host esta en :$relayPort (archivo: ops/whatsapp_relay.py)." -ForegroundColor White
Write-Host "Para parar el relay: if (Test-Path '$here\relay.pid') { Stop-Process -Id (Get-Content '$here\relay.pid') -Force }" -ForegroundColor White
