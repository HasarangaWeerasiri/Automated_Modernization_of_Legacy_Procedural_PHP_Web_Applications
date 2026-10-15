<#
.SYNOPSIS
  E2 live part: load each ORIGINAL schema.sql into MySQL 8, reflect it and
  compare it with the generated models (plus the offline views A/B/C).

.DESCRIPTION
  Reuses the E3 databases c3_e3_<app> (empty after the E3 downgrade): each is
  dropped, recreated and loaded with the original DDL. No new grants needed.
  The password is read from $env:C3_DB_PASSWORD (session only; never written).
  Reports go to evidence/mysql_runs/<timestamp>/e2/ (never overwritten).

.EXAMPLE
  $env:C3_DB_PASSWORD = Read-Host "c3_tester password"
  .\scripts\run_e2_mysql.ps1
#>

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Component = Join-Path $Root "components\c3_data_layer"
$Python = Join-Path $Component ".venv\Scripts\python.exe"

if (-not $env:C3_DB_PASSWORD) {
    throw 'C3_DB_PASSWORD is not set. Run: $env:C3_DB_PASSWORD = Read-Host "c3_tester password"'
}

$Stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$EvidenceDir = Join-Path $Root "evidence\mysql_runs\$Stamp\e2"
if (Test-Path $EvidenceDir) { throw "Evidence folder already exists: $EvidenceDir" }
New-Item -ItemType Directory -Force $EvidenceDir | Out-Null

$env:PYTHONPATH = Join-Path $Component "src"
$env:PYTHONDONTWRITEBYTECODE = "1"
$Log = Join-Path $EvidenceDir "run_e2.log"

$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $Python (Join-Path $Root "scripts\schema_comparison.py") --mysql --out $EvidenceDir 2>&1 |
        ForEach-Object { "$_" } | Out-File -Encoding utf8 $Log
    $code = $LASTEXITCODE
} finally { $ErrorActionPreference = $previous }

Get-Content $Log | Select-String -NotMatch '^\['
Write-Host "`nExit code $code. Reports in $EvidenceDir. Paste the app lines above back to Claude."
