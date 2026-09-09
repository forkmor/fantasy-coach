$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$output = Join-Path $root "dist\fieldhouse-hostinger-vps.zip"
$stage = Join-Path $env:TEMP "fieldhouse-hostinger-vps-package"

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage "backend") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage "frontend") | Out-Null

Copy-Item (Join-Path $root "backend\app") (Join-Path $stage "backend\app") -Recurse
Copy-Item (Join-Path $root "backend\requirements*.txt") (Join-Path $stage "backend")
Copy-Item (Join-Path $root "frontend\src") (Join-Path $stage "frontend\src") -Recurse
foreach ($name in @("package.json", "package-lock.json", "index.html", "tsconfig.json", "vite.config.ts")) {
    Copy-Item (Join-Path $root "frontend\$name") (Join-Path $stage "frontend\$name")
}
New-Item -ItemType Directory -Path (Join-Path $stage "deploy") | Out-Null
foreach ($name in @("generate-config.py", "install.sh", "update.sh", "backup.sh", "fieldhouse.nginx")) {
    Copy-Item (Join-Path $root "deploy\$name") (Join-Path $stage "deploy\$name")
}
foreach ($name in @("install.sh", "update.sh", "backup.sh")) {
    $path = Join-Path $stage "deploy\$name"
    $content = [IO.File]::ReadAllText($path).Replace("`r`n", "`n")
    [IO.File]::WriteAllText($path, $content, [Text.UTF8Encoding]::new($false))
}
foreach ($name in @("Dockerfile", "compose.yaml", "compose.nginx.yaml", "Caddyfile", ".dockerignore", ".env.example", "README.md", "DEPLOYMENT.md")) {
    Copy-Item (Join-Path $root $name) (Join-Path $stage $name)
}

New-Item -ItemType Directory -Path (Split-Path -Parent $output) -Force | Out-Null
if (Test-Path $output) { Remove-Item -Force $output }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $output -CompressionLevel Optimal
Remove-Item -Recurse -Force $stage
Write-Host "Created $output"
