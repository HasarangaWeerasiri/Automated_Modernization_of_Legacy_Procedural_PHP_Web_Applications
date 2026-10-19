# Start the C4 validation dashboard on http://127.0.0.1:8800 (this machine only).
#
#   dashboard\start.ps1
#
# The first time, it creates the dashboard's own environment (dashboard\.venv) from the
# engine's Python and installs requirements.txt. The engine's environment is not touched.
# Port 8800, because the migrated system's API uses 8000.

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$venv = Join-Path $here ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    $engine = Join-Path (Split-Path $here -Parent) ".venv\Scripts\python.exe"
    $base = if (Test-Path $engine) { & $engine -c "import sys; print(sys.base_prefix)" } else { $null }
    $creator = if ($base) { Join-Path $base "python.exe" } else { "python" }
    & $creator -m venv $venv
    & $python -m pip install --quiet -r (Join-Path $here "requirements.txt")
}

Write-Host "C4 validation dashboard: http://127.0.0.1:8800"
& $python -m uvicorn server:app --app-dir $here --host 127.0.0.1 --port 8800
