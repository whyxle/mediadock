$ErrorActionPreference = "SilentlyContinue"

$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $AppRoot
$pidPath = Join-Path $AppRoot "data\server.pid"

if (Test-Path $pidPath) {
    $serverPid = Get-Content $pidPath | Select-Object -First 1
    if ($serverPid) {
        Stop-Process -Id ([int]$serverPid) -Force
        Write-Host "Остановлен сервер Video Downloader, PID: $serverPid"
    }
}

$denoPath = Join-Path $WorkspaceRoot "deno\deno.exe"
$listeners = netstat -ano | Select-String ":4416" | Select-String "LISTENING"
foreach ($listener in $listeners) {
    $parts = ($listener.Line -split "\s+") | Where-Object { $_ }
    $pid = [int]$parts[-1]
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $pid"
    if ($process.CommandLine -like "*$denoPath*") {
        Stop-Process -Id $pid -Force
        Write-Host "Остановлен PO-token provider, PID: $pid"
    }
}
