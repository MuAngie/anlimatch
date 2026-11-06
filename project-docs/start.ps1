$ErrorActionPreference = 'Stop'
$docsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$address = 'http://127.0.0.1:8790'
try {
    $existingDocs = Invoke-WebRequest -Uri $address -UseBasicParsing -TimeoutSec 2
    if ($existingDocs.Content.Contains('PROJECT HANDBOOK / 01')) {
        Start-Process $address
        exit 0
    }
    throw 'Port 8790 is in use by another application.'
} catch {
    if ($_.Exception.Message -like '*in use*') { throw }
}
$nodeExe = (Get-Command node -ErrorAction Stop).Source
$docsProcess = Start-Process -FilePath $nodeExe -ArgumentList 'server.cjs' -WorkingDirectory $docsRoot -WindowStyle Hidden -PassThru
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri $address -UseBasicParsing -TimeoutSec 2
        if ($response.Content.Contains('PROJECT HANDBOOK / 01')) {
            Write-Host "Project Handbook is running (PID $($docsProcess.Id)): $address"
            Start-Process $address
            exit 0
        }
    } catch { }
    Start-Sleep -Milliseconds 200
}
throw 'Cannot start the project handbook. Check Node.js and port 8790.'
