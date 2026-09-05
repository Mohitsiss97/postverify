# Start, stop and inspect both services for local and Tailscale testing.
#
# This is a development convenience only. Production runs the containers in
# campaign-portal/docker-compose.yml; see docs/deployment.md.
#
#   .\dev.ps1 start | stop | restart | status | logs
#
# Two decisions here were paid for the hard way.
#
# Both services bind to 127.0.0.1 and every route in from another machine is a
# Tailscale proxy. A service bound to 0.0.0.0 has to be let through the Windows
# firewall, and that stayed unreachable even with an Allow rule in place and the
# Tailscale adapter classified Private. Traffic through tailscaled takes the
# firewall out of the path entirely, because tailscaled has been allowed since
# it was installed. It also keeps both services off the local Wi-Fi.
#
# The services run hidden with their output redirected to logs/, rather than in
# console windows. Windows are easy to close by accident, and when they close
# the services die silently everywhere at once — which looks exactly like a
# network fault and is not one.

param([Parameter(Position = 0)][string]$Action = "status")

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Tailscale = "C:\Program Files\Tailscale\tailscale.exe"
$LogDir = Join-Path $Root "logs"
$EnginePort = 8200
$PortalPort = 8300
$ProxyPorts = @(8300, 8080)      # HTTP routes to the portal, matched on hostname
$EngineProxyPort = 8201          # HTTP route to the engine, matched on hostname
$PortalTcpPort = 8400            # TCP forward to the portal, works by IP too
$EngineTcpPort = 8401            # TCP forward to the engine, works by IP too

function Get-PidOnPort($Port) {
    # Loopback only. The tailnet listener on the same port belongs to
    # tailscaled, and killing that takes Tailscale itself down.
    $line = netstat -ano | Select-String "127\.0\.0\.1:$Port\s.*LISTENING" | Select-Object -First 1
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
            Write-Host "  $Name up on $Port" -ForegroundColor Green
            return $true
        } catch { Start-Sleep -Seconds 1 }
    }
    Write-Host "  $Name did not start on $Port" -ForegroundColor Red
    Write-Host "  Run '.\dev.ps1 logs' to see why." -ForegroundColor Yellow
    return $false
}

function Start-One($Name, $Dir, $Port) {
    if (Get-PidOnPort $Port) {
        Write-Host "  $Name already running on $Port" -ForegroundColor DarkGray
        return
    }
    Start-Process -FilePath "python" `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--port", $Port `
        -WorkingDirectory (Join-Path $Root $Dir) `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "$Name.log") `
        -RedirectStandardError  (Join-Path $LogDir "$Name.err.log")
    Wait-ForPort $Port $Name | Out-Null
}

function Start-Services {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    # The engine first: the portal calls it, and reports it as down until it is
    # up.
    Start-One "engine" "postverify-api" $EnginePort

    # Set on this process so the child inherits them; Start-Process has no way
    # to pass environment variables directly.
    $env:ADMIN_TOKEN = Get-AdminToken
    $env:ENGINE_URL = "http://localhost:$EnginePort"
    Start-One "portal" "campaign-portal" $PortalPort

    if (Test-Path $Tailscale) {
        # Which ports tailscaled is actually listening on. Reading this from the
        # text output also matched each route's proxy *target*, so a route to
        # 127.0.0.1:8300 looked like a listener on 8300 and the real one was
        # never created. The JSON says exactly what is listening.
        $listening = @()
        try {
            $cfg = & $Tailscale serve status --json 2>$null | ConvertFrom-Json
            if ($cfg.TCP) { $listening = $cfg.TCP.PSObject.Properties.Name }
        } catch { }

        foreach ($p in $ProxyPorts) {
            if ($listening -notcontains "$p") {
                & $Tailscale serve --bg --http=$p "http://127.0.0.1:$PortalPort" | Out-Null
                Write-Host "  tailnet proxy on $p -> portal" -ForegroundColor Green
            }
        }
        if ($listening -notcontains "$EngineProxyPort") {
            & $Tailscale serve --bg --http=$EngineProxyPort "http://127.0.0.1:$EnginePort" | Out-Null
            Write-Host "  tailnet proxy on $EngineProxyPort -> engine" -ForegroundColor Green
        }

        # An --http route is matched on the Host header, and the header it is
        # registered under is the node's DNS name. A request to
        # http://100.x.x.x:8300/ carries the IP as its Host and matches nothing,
        # so it never reaches the app — which looks exactly like the machine
        # being unreachable. A --tcp forward does not read the request at all,
        # so it works whether the caller uses the name or the IP.
        if ($listening -notcontains "$PortalTcpPort") {
            & $Tailscale serve --bg --tcp=$PortalTcpPort "tcp://127.0.0.1:$PortalPort" | Out-Null
            Write-Host "  tailnet TCP forward on $PortalTcpPort -> portal" -ForegroundColor Green
        }
        if ($listening -notcontains "$EngineTcpPort") {
            & $Tailscale serve --bg --tcp=$EngineTcpPort "tcp://127.0.0.1:$EnginePort" | Out-Null
            Write-Host "  tailnet TCP forward on $EngineTcpPort -> engine" -ForegroundColor Green
        }
    }
    Show-Status
}

function Stop-Services {
    foreach ($p in @($PortalPort, $EnginePort)) {   # portal first: it calls the engine
        $procId = Get-PidOnPort $p
        if ($procId) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Write-Host "  stopped $p (pid $procId)" -ForegroundColor Yellow
        } else {
            Write-Host "  nothing on $p" -ForegroundColor DarkGray
        }
    }
    Write-Host "`n  The tailnet proxies stay in place. To remove them:" -ForegroundColor DarkGray
    Write-Host "    tailscale serve reset" -ForegroundColor DarkGray
}

function Show-Logs {
    foreach ($n in @("engine", "portal")) {
        foreach ($suffix in @(".log", ".err.log")) {
            $f = Join-Path $LogDir "$n$suffix"
            if ((Test-Path $f) -and (Get-Item $f).Length -gt 0) {
                Write-Host "`n--- $n$suffix (last 15 lines) ---" -ForegroundColor Cyan
                Get-Content $f -Tail 15
            }
        }
    }
    Write-Host ""
}

function Show-Status {
    Write-Host "`nServices" -ForegroundColor Cyan
    foreach ($s in @(@{n = "engine"; p = $EnginePort }, @{n = "portal"; p = $PortalPort })) {
        $procId = Get-PidOnPort $s.p
        if (-not $procId) {
            Write-Host ("  {0,-7} {1}  down" -f $s.n, $s.p) -ForegroundColor Red
            continue
        }
        try {
            Invoke-WebRequest "http://127.0.0.1:$($s.p)/ready" -TimeoutSec 20 -UseBasicParsing | Out-Null
            Write-Host ("  {0,-7} {1}  ready   (pid {2})" -f $s.n, $s.p, $procId) -ForegroundColor Green
        } catch {
            Write-Host ("  {0,-7} {1}  running, not ready (pid {2})" -f $s.n, $s.p, $procId) -ForegroundColor Yellow
        }
    }

    Write-Host "`nOn this machine" -ForegroundColor Cyan
    Write-Host "  http://localhost:$PortalPort/"

    if (Test-Path $Tailscale) {
        $ip = (& $Tailscale ip -4 2>$null | Select-Object -First 1)
        $listening = @()
        try {
            $cfg = & $Tailscale serve status --json 2>$null | ConvertFrom-Json
            if ($cfg.TCP) { $listening = $cfg.TCP.PSObject.Properties.Name }
        } catch { }
        if ($ip -and $listening.Count) {
            Write-Host "`nTailnet ports listening: $($listening -join ', ')" -ForegroundColor DarkGray
            $dns = (& $Tailscale status --json 2>$null | ConvertFrom-Json).Self.DNSName.TrimEnd('.')
            Write-Host "`nFrom any other machine on the tailnet" -ForegroundColor Cyan
            Write-Host "  By IP - these work from anything:" -ForegroundColor Green
            Write-Host "    Portal, the app     http://${ip}:$PortalTcpPort/"
            Write-Host "    Portal, API docs    http://${ip}:$PortalTcpPort/docs"
            Write-Host "    Engine, API docs    http://${ip}:$EngineTcpPort/docs"
            if ($dns) {
                Write-Host "  By name - only where MagicDNS resolves:" -ForegroundColor DarkGray
                Write-Host "    Portal              http://${dns}:$PortalPort/"
                Write-Host "    Engine              http://${dns}:$EngineProxyPort/docs"
            }
            Write-Host ""
            Write-Host "  The IP addresses use a TCP forward, which ignores the Host" -ForegroundColor DarkGray
            Write-Host "  header. The named ones are HTTP routes registered under the" -ForegroundColor DarkGray
            Write-Host "  node's DNS name, so an IP in the address bar matches nothing" -ForegroundColor DarkGray
            Write-Host "  and the request never arrives." -ForegroundColor DarkGray
            Write-Host "  Always keep the explicit port: a bare hostname gets upgraded" -ForegroundColor DarkGray
            Write-Host "  to HTTPS, and this tailnet has no TLS certificate." -ForegroundColor DarkGray
        } elseif ($ip) {
            Write-Host "`nNo tailnet proxy configured. Run '.\dev.ps1 start'." -ForegroundColor Yellow
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
    "restart" { Stop-Services; Start-Sleep -Seconds 3; Start-Services }
    "status" { Show-Status }
    "logs" { Show-Logs }
    default { Write-Host "Usage: .\dev.ps1 [start|stop|restart|status|logs]" }
}
