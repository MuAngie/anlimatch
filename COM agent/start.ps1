$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$bundlePython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$pythonExe = if (Test-Path -LiteralPath $bundlePython) { $bundlePython } else { (Get-Command python -ErrorAction Stop).Source }
$address = 'http://127.0.0.1:8765'
try {
    $existingApp = Invoke-RestMethod -Uri "$address/api/bootstrap" -TimeoutSec 2
    if ($existingApp.demo_catalog -and $existingApp.today) {
        Start-Process $address
        Write-Host 'Health Composer is already running.'
        exit 0
    }
} catch { }
$serverFile = Join-Path $appRoot 'server.py'
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
$commandLine = '"{0}" "{1}" --port 8765' -f $pythonExe, $serverFile
$started = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $commandLine
    CurrentDirectory = $appRoot
    ProcessStartupInformation = $startup
}
if ($started.ReturnValue -ne 0) { throw "Cannot start Health Composer (code $($started.ReturnValue))." }
$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try {
        $appResponse = Invoke-RestMethod -Uri "$address/api/bootstrap" -TimeoutSec 2
        if ($appResponse.demo_catalog -and $appResponse.today) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 250 }
}
if (-not $ready) { throw 'Health Composer did not become ready. Check Python and openpyxl installation.' }
Write-Host "Health Composer is running (PID $($started.ProcessId)): $address"
Start-Process $address
