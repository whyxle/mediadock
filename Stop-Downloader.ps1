$ErrorActionPreference = "SilentlyContinue"

$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $AppRoot
$pidPath = Join-Path $AppRoot "data\server.pid"

if (Test-Path $pidPath) {
    $serverPid = Get-Content $pidPath | Select-Object -First 1
    if ($serverPid) {
        Stop-Process -Id ([int]$serverPid) -Force
        Write-Host "Stopped MediaDock server, PID: $serverPid"
    }
}

$denoPath = Join-Path $WorkspaceRoot "deno\deno.exe"
$listeners = netstat -ano | Select-String ":4416" | Select-String "LISTENING"
foreach ($listener in $listeners) {
    $parts = ($listener.Line -split "\s+") | Where-Object { $_ }
    $listenerPid = [int]$parts[-1]
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid"
    if ($process.CommandLine -like "*$denoPath*") {
        Stop-Process -Id $listenerPid -Force
        Write-Host "Stopped PO-token provider, PID: $listenerPid"
    }
}
