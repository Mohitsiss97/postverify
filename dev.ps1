# Start, stop and inspect both services for local and Tailscale testing.
#
# This is a development convenience only. Production runs the containers in
# campaign-portal/docker-compose.yml; see docs/deployment.md.
#
#   .\dev.ps1 start     both services, plus the Tailscale proxy
#   .\dev.ps1 stop      both services (the Tailscale proxy is left alone)
#   .\dev.ps1 status    what is running, and the URLs to open
#   .\dev.ps1 restart
#
# The engine is started first because the portal calls it. Both bind to
# 127.0.0.1 only: the sole route in from another machine is Tailscale, so
# nothing is exposed on the local Wi-Fi.

param([Parameter(Position = 0)][string]$Action = "status")

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Tailscale = "C:\Program Files\Tailscale\tailscale.exe"
$EnginePort = 8200
$PortalPort = 8300

function Get-PidOnPort($Port) {
    $line = netstat -ano | Select-String ":$Port\s.*LISTENING" | Select-Object -First 1
    if ($line) { ($line -split '\s+')[-1] } else { $null }
}

function Get-AdminToken {
    $file = Join-Path $Root ".admin-token.txt"
    if (Test-Path $file) { (Get-Content $file -Raw).Trim() } else { "" }
}

function Wait-ForPort($Port, $Name, $Seconds = 45) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        try {
            Invoke-WebRequest "http://127.0.0.1:$Port/health" -TimeoutSec 3 -UseBasicParsing | Out-Null
            Write-Host "  $Name is up on $Port" -ForegroundColor Green
            return $true
        } catch { Start-Sleep -Seconds 1 }
    }
    Write-Host "  $Name did not come up on $Port" -ForegroundColor Red
    Write-Host "  Its window is still open; read the error there." -ForegroundColor Yellow
    return $false
}

function Start-Services {
    if (Get-PidOnPort $EnginePort) {
        Write-Host "  engine already running on $EnginePort" -ForegroundColor DarkGray
    } else {
        # Each service gets its own window so its log stays readable and either
        # can be restarted without disturbing the other.
        Start-Process powershell -ArgumentList "-NoExit", "-Command",
            "cd '$Root\postverify-api'; python -m uvicorn app.main:app --port $EnginePort"
        Wait-ForPort $EnginePort "engine" | Out-Null
    }

    if (Get-PidOnPort $PortalPort) {
        Write-Host "  portal already running on $PortalPort" -ForegroundColor DarkGray
    } else {
        $token = Get-AdminToken
        # Built as one string first: inside an -ArgumentList array, a trailing
        # "+" is read as another positional argument rather than as
        # concatenation.
        $portalCmd = "cd '$Root\campaign-portal'; " +
            "`$env:ADMIN_TOKEN='$token'; " +
            "`$env:ENGINE_URL='http://localhost:$EnginePort'; " +
            "python -m uvicorn app.main:app --port $PortalPort"
        Start-Process powershell -ArgumentList "-NoExit", "-Command", $portalCmd
        Wait-ForPort $PortalPort "portal" | Out-Null
    }

    # The proxy survives reboots on its own, so this only re-applies it if the
    # configuration was cleared.
    if (Test-Path $Tailscale) {
        $serve = & $Tailscale serve status 2>&1 | Out-String
        if ($serve -notmatch "8080") {
            & $Tailscale serve --bg --http=8080 "http://127.0.0.1:$PortalPort" | Out-Null
            Write-Host "  tailscale proxy re-applied on 8080" -ForegroundColor Green
        }
    }
    Show-Status
}

function Stop-Services {
    foreach ($p in @($PortalPort, $EnginePort)) {     # portal first: it calls the engine
        $procId = Get-PidOnPort $p
        if ($procId) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Write-Host "  stopped $p (pid $procId)" -ForegroundColor Yellow
        } else {
            Write-Host "  nothing on $p" -ForegroundColor DarkGray
        }
    }
    Write-Host "`n  The Tailscale proxy is left in place. To remove it:" -ForegroundColor DarkGray
    Write-Host "    tailscale serve --http=8080 off" -ForegroundColor DarkGray
}

function Show-Status {
    Write-Host "`nServices" -ForegroundColor Cyan
    foreach ($s in @(@{n = "engine"; p = $EnginePort }, @{n = "portal"; p = $PortalPort })) {
        $procId = Get-PidOnPort $s.p
        if ($procId) {
            try {
                $r = Invoke-WebRequest "http://127.0.0.1:$($s.p)/ready" -TimeoutSec 20 -UseBasicParsing
                Write-Host ("  {0,-7} {1}  ready" -f $s.n, $s.p) -ForegroundColor Green
            } catch {
                Write-Host ("  {0,-7} {1}  running, not ready" -f $s.n, $s.p) -ForegroundColor Yellow
            }
        } else {
            Write-Host ("  {0,-7} {1}  down" -f $s.n, $s.p) -ForegroundColor Red
        }
    }

    Write-Host "`nOpen from this machine" -ForegroundColor Cyan
    Write-Host "  http://localhost:$PortalPort"

    if (Test-Path $Tailscale) {
        $ip = (& $Tailscale ip -4 2>$null | Select-Object -First 1)
        if ($ip) {
            Write-Host "`nOpen from another machine on the tailnet" -ForegroundColor Cyan
            Write-Host "  http://${ip}:8080        <- use this one first"
            Write-Host "  http://${ip}"
            Write-Host "  An address with an explicit port stops the browser" -ForegroundColor DarkGray
            Write-Host "  upgrading it to HTTPS, which this tailnet cannot serve." -ForegroundColor DarkGray
        }
    }

    $token = Get-AdminToken
    if ($token) {
        Write-Host "`nAdmin token (paste into the field at the top right)" -ForegroundColor Cyan
        Write-Host "  $token"
    }
    Write-Host ""
}

switch ($Action.ToLower()) {
    "start" { Start-Services }
    "stop" { Stop-Services }
    "restart" { Stop-Services; Start-Sleep -Seconds 2; Start-Services }
    "status" { Show-Status }
    default {
        Write-Host "Usage: .\dev.ps1 [start|stop|restart|status]"
    }
}
