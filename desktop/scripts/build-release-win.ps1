$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Backend = Split-Path -Parent $Root
$Dist = Join-Path $Root "dist"
$Version = (Get-Content (Join-Path $Root "package.json") -Raw | ConvertFrom-Json).version
$ArtifactGlob = "Hoplyra-$Version-*-portable.exe"

Write-Host "==> Cleaning previous build artifacts..."
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
  (Join-Path $Root "release"), `
  (Join-Path $Root "resources\backend"), `
  $Dist
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

Write-Host "==> Building frontend..."
Push-Location $Root
npm run build:frontend
Pop-Location

Write-Host "==> Building backend binary..."
Push-Location $Backend
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "==> Creating backend venv..."
  python -m venv .venv
  .\.venv\Scripts\pip install -r requirements.txt
}
.\.venv\Scripts\pip install -q -r requirements-build.txt
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
.\.venv\Scripts\pyinstaller --noconfirm hoplyra-backend.spec

$Resources = Join-Path $Root "resources\backend"
New-Item -ItemType Directory -Force -Path $Resources | Out-Null
Copy-Item -Force (Join-Path $Backend "dist\hoplyra-backend.exe") $Resources
Pop-Location

Write-Host "==> Packaging Windows portable..."
Push-Location $Root
npx electron-builder --win portable --publish never
Pop-Location

$Built = Get-ChildItem -Path $Dist -Filter $ArtifactGlob -File -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Built) {
  $Built = Get-ChildItem -Path $Dist -Filter "Hoplyra-$Version-*.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $Built) {
  throw "Windows portable not found in dist (expected $ArtifactGlob)"
}

$Hash = (python -c "import hashlib, sys; print(hashlib.file_digest(open(sys.argv[1], 'rb'), 'sha256').hexdigest())" "$($Built.FullName)").Trim()
"$Hash  $($Built.Name)" | Out-File -Encoding ascii (Join-Path $Dist "SHA256SUMS")

@"
Hoplyra $Version — Windows x64 portable

Run:
  $($Built.Name)

Data directory:
  %APPDATA%\hoplyra-desktop\hoplyra-data\

Default login: admin / admin

SHA256: $Hash
"@ | Out-File -Encoding utf8 (Join-Path $Dist "README.txt")

Write-Host ""
Write-Host "==> Done: $($Built.FullName)"
Write-Host "    Size: $([math]::Round($Built.Length / 1MB, 1)) MB"
Write-Host "    SHA256: $Hash"
