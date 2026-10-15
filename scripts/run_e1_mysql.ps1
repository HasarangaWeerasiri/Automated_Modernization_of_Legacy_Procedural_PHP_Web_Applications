<#
.SYNOPSIS
  E1: PHP versus generated SQLAlchemy equivalence on MySQL 8, per SQL site.

.DESCRIPTION
  Runs scripts/equivalence/run_e1.py for every benchmark app (Clinic has its
  own equivalence tests). For each converted site and each complete input case
  in benchmarks/equivalence_cases/<app>.json, both c3_e1_<app>_legacy and
  c3_e1_<app>_migrated are rebuilt from schema + seed, the legacy SQL is built
  by PHP from the original source and run with mysqli, the generated function
  is run with SQLAlchemy, and the results are compared.

  Prerequisites (once): the c3_e1_<app>_legacy / _migrated databases and
  grants from scripts/e1_create_databases.sql, run in MySQL Workbench.

  The password is read from $env:C3_DB_PASSWORD (session only; never written).
  Reports go to evidence/mysql_runs/<timestamp>/e1/ (never overwritten).

.EXAMPLE
  $env:C3_DB_PASSWORD = Read-Host "c3_tester password"
  .\scripts\run_e1_mysql.ps1
#>

param([string]$Apps = "simple_crud,mixed_crud,hard_crud,helpdesk,hms_kishan0725,news_buzz,fee_rainbow,dignity_dev_hms")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Component = Join-Path $Root "components\c3_data_layer"
$Python = Join-Path $Component ".venv\Scripts\python.exe"

if (-not $env:C3_DB_PASSWORD) {
    throw 'C3_DB_PASSWORD is not set. Run: $env:C3_DB_PASSWORD = Read-Host "c3_tester password"'
}

$Stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$EvidenceDir = Join-Path $Root "evidence\mysql_runs\$Stamp\e1"
if (Test-Path $EvidenceDir) { throw "Evidence folder already exists: $EvidenceDir" }
New-Item -ItemType Directory -Force $EvidenceDir | Out-Null

$env:PYTHONPATH = Join-Path $Component "src"
$env:PYTHONDONTWRITEBYTECODE = "1"
$Log = Join-Path $EvidenceDir "run_e1.log"

$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $Python (Join-Path $Root "scripts\equivalence\run_e1.py") run --apps $Apps --out $EvidenceDir 2>&1 |
        ForEach-Object { "$_" } | Tee-Object -Variable lines | Out-Null
    $code = $LASTEXITCODE
} finally { $ErrorActionPreference = $previous }
$lines | Out-File -Encoding utf8 $Log

Get-Content -Tail 40 $Log
Write-Host "`nExit code $code. Reports in $EvidenceDir. Paste php_vs_sqlalchemy_equivalence.json (it is short) back to Claude."
