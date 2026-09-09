$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment." }
}
& ".\.venv\Scripts\python.exe" -m pip install -r ".\backend\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }
Push-Location ".\frontend"
try {
    if (Test-Path "package-lock.json") {
        npm.cmd ci
    } else {
        npm.cmd install
    }
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
} finally {
    Pop-Location
}
Write-Host "Setup complete. Run .\start.ps1 to open the app."
