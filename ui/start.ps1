# Start the group UI on http://127.0.0.1:8000 (run from the repository root: ui\start.ps1).
# The first time it creates ui\.venv, installs the server's requirements and builds the web app.
$ErrorActionPreference = "Stop"
$ui = $PSScriptRoot
$python = Join-Path $ui ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creating ui\.venv (Python 3.12)..."
    py -3.12 -m venv (Join-Path $ui ".venv")
    & $python -m pip install -q -r (Join-Path $ui "server\requirements.txt")
}
if (-not (Test-Path (Join-Path $ui "web\dist\index.html"))) {
    Write-Host "Building the web app..."
    Push-Location (Join-Path $ui "web")
    try { npm ci; if ($?) { npm run build } } finally { Pop-Location }
}
Write-Host "UI: http://127.0.0.1:8000  (Ctrl+C to stop)"
& $python -m uvicorn main:app --app-dir (Join-Path $ui "server") --host 127.0.0.1 --port 8000
