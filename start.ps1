$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Run .\setup.ps1 first."
}
if (-not (Test-Path "frontend\dist\index.html")) {
    throw "Frontend is not built. Run .\setup.ps1 first."
}
& ".\.venv\Scripts\python.exe" ".\backend\launch.py"
if ($LASTEXITCODE -ne 0) { throw "The harness exited with an error." }
