$ErrorActionPreference = "Stop"

$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $AppRoot

function Get-Python314 {
    try {
        $path = (& py -3.14 -c "import sys; print(sys.executable)" 2>$null).Trim()
        if ($path -and (Test-Path $path)) { return $path }
    } catch {}

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }

    throw "Python 3.14 не найден. Установите Python 3.14 или добавьте python в PATH."
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
    throw "Не удалось найти свободный порт рядом с $StartPort."
}

$python = Get-Python314
$denoPath = Join-Path $WorkspaceRoot "deno\deno.exe"
if (Test-Path $denoPath) {
    $env:PATH = (Split-Path -Parent $denoPath) + ";" + $env:PATH
}

Write-Host "Проверяю инструменты..."
& $python -m yt_dlp --version | Out-Host
ffmpeg -version | Select-Object -First 1 | Out-Host
if (Test-Path $denoPath) {
    & $denoPath --version | Select-Object -First 1 | Out-Host
}

$port = Get-FreePort 8765
$url = "http://127.0.0.1:$port"
$serverPath = Join-Path $AppRoot "server.py"
$pidPath = Join-Path $AppRoot "data\server.pid"

Write-Host "Запускаю Video Downloader на $url"
$arguments = @("`"$serverPath`"", "--host", "127.0.0.1", "--port", "$port")
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $AppRoot -WindowStyle Hidden -PassThru
$process.Id | Set-Content -Path $pidPath -Encoding ASCII

Start-Sleep -Seconds 1
Start-Process $url
Write-Host "Готово. Сервер работает в фоне, PID: $($process.Id)"
