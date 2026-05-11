$ErrorActionPreference = "Stop"

$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $AppRoot

function Wait-BeforeClose {
    if ($Host.Name -like "*ConsoleHost*") {
        Write-Host ""
        Read-Host "Press Enter to close this window"
    }
}

function Get-Python314 {
    try {
        $path = (& py -3.14 -c "import sys; print(sys.executable)" 2>$null).Trim()
        if ($path -and (Test-Path $path)) { return $path }
    } catch {}

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }

    throw "Python 3.14 was not found. Install Python 3.14 or add python to PATH."
}

function Get-FreePort {
    param([int]$StartPort = 8765)

    for ($port = $StartPort; $port -lt ($StartPort + 30); $port++) {
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $client.Connect("127.0.0.1", $port)
        } catch {
            return $port
        } finally {
            $client.Dispose()
        }
    }
    throw "Could not find a free port near $StartPort."
}

function Get-ListeningPid {
    param([int]$Port)

    $listener = netstat -ano | Select-String ":$Port" | Select-String "LISTENING" | Select-Object -First 1
    if (-not $listener) { return $null }

    $parts = ($listener.Line -split "\s+") | Where-Object { $_ }
    if (-not $parts) { return $null }

    return [int]$parts[-1]
}

function Test-MediaDockPort {
    param([int]$Port)

    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/settings" -TimeoutSec 2
        return ($null -ne $response.settings)
    } catch {
        return $false
    }
}

function Stop-PidFileServer {
    param([string]$PidPath)

    if (-not (Test-Path $PidPath)) { return }

    $serverPid = Get-Content $PidPath | Select-Object -First 1
    if (-not $serverPid) { return }

    $process = Get-Process -Id ([int]$serverPid) -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host "Stopping previous MediaDock server, PID: $serverPid"
        Stop-Process -Id ([int]$serverPid) -Force
        Start-Sleep -Milliseconds 500
    }
}

function Stop-MediaDockOnPort {
    param([int]$Port)

    $listenerPid = Get-ListeningPid $Port
    if (-not $listenerPid) { return }
    if (-not (Test-MediaDockPort $Port)) { return }

    Write-Host "Stopping MediaDock already running on port $Port, PID: $listenerPid"
    Stop-Process -Id $listenerPid -Force
    Start-Sleep -Milliseconds 500
}

try {
    $python = Get-Python314
    $denoPath = Join-Path $WorkspaceRoot "deno\deno.exe"
    $pidPath = Join-Path $AppRoot "data\server.pid"

    Stop-PidFileServer $pidPath
    Stop-MediaDockOnPort 8765

    if (Test-Path $denoPath) {
        $env:PATH = (Split-Path -Parent $denoPath) + ";" + $env:PATH
    }

    Write-Host "Checking tools..."
    & $python -m yt_dlp --version | Out-Host
    ffmpeg -version 2>&1 | Select-Object -First 1 | Out-Host
    if (Test-Path $denoPath) {
        & $denoPath --version | Select-Object -First 1 | Out-Host
    }

    $port = Get-FreePort 8765
    $url = "http://127.0.0.1:$port"
    $serverPath = Join-Path $AppRoot "server.py"

    Write-Host "Starting MediaDock at $url"
    $arguments = @("`"$serverPath`"", "--host", "127.0.0.1", "--port", "$port")
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $AppRoot -WindowStyle Hidden -PassThru
    $process.Id | Set-Content -Path $pidPath -Encoding ASCII

    Start-Sleep -Seconds 1
    Start-Process $url
    Write-Host "Done. Server is running in the background, PID: $($process.Id)"
} catch {
    Write-Host ""
    Write-Host "Startup failed:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.ScriptStackTrace) {
        Write-Host ""
        Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    }
    Wait-BeforeClose
    exit 1
}
