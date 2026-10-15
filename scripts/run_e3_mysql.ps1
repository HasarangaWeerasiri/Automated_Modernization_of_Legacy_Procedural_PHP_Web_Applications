<#
.SYNOPSIS
  E3: Alembic round trip on MySQL 8 for every benchmark app.

.DESCRIPTION
  For each app: run the current C3 pipeline into a temp folder, drop and
  recreate the empty database c3_e3_<app>, then run
  scripts/alembic_roundtrip.py (upgrade head -> reflect and compare with the
  models -> alembic check -> downgrade base).

  Prerequisites (once): the c3_e3_<app> databases and grants from
  scripts/e1_create_databases.sql, run in MySQL Workbench as an admin.

  The password is read from $env:C3_DB_PASSWORD (session only; never written).
  Reports go to evidence/mysql_runs/<timestamp>/e3/ (never overwritten).

.EXAMPLE
  $env:C3_DB_PASSWORD = Read-Host "c3_tester password"
  .\scripts\run_e3_mysql.ps1
#>

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Component = Join-Path $Root "components\c3_data_layer"
$Python = Join-Path $Component ".venv\Scripts\python.exe"
$MySql = "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"
$Apps = Join-Path $Root "benchmarks\apps"

if (-not $env:C3_DB_PASSWORD) {
    throw 'C3_DB_PASSWORD is not set. Run: $env:C3_DB_PASSWORD = Read-Host "c3_tester password"'
}

$Stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$EvidenceDir = Join-Path $Root "evidence\mysql_runs\$Stamp\e3"
if (Test-Path $EvidenceDir) { throw "Evidence folder already exists: $EvidenceDir" }
New-Item -ItemType Directory -Force $EvidenceDir | Out-Null
$WorkDir = Join-Path $env:TEMP "c3_e3_$Stamp"

$Benchmarks = [ordered]@{
    "simple_crud"     = @("$Apps\simple_crud\legacy", "$Apps\simple_crud\schema.sql")
    "mixed_crud"      = @("$Apps\mixed_crud\legacy", "$Apps\mixed_crud\schema.sql")
    "hard_crud"       = @("$Apps\hard_crud\legacy", "$Apps\hard_crud\schema.sql")
    "clinic"          = @("$Apps\clinic_inventory_legacy_hard\legacy", "$Apps\clinic_inventory_legacy_hard\database\schema.sql")
    "helpdesk"        = @("$Apps\helpdesk_legacy_holdout\legacy", "$Apps\helpdesk_legacy_holdout\database\schema.sql")
    "hms_kishan0725"  = @("$Apps\_app_only\hms_kishan0725", "$Apps\hms_kishan0725\myhmsdb.sql")
    "news_buzz"       = @("$Apps\_app_only\news_buzz", "$Apps\news_buzz\db\NEWSBUZZ.sql")
    "fee_rainbow"     = @("$Apps\_app_only\fee_rainbow", "$Apps\fee_rainbow\database\db.sql")
    "dignity_dev_hms" = @("$Apps\_app_only\dignity_dev_hms", "$Apps\dignity_dev_hms\127_0_0_1.sql")
}

$env:PYTHONPATH = Join-Path $Component "src"
$env:PYTHONDONTWRITEBYTECODE = "1"
$Head = git -C $Root rev-parse HEAD
$Results = @()

# Run a native command, writing stdout+stderr to a UTF-8 log. Alembic logs to
# stderr, which must not abort the script; the exit code is returned.
function Invoke-Logged([string]$LogFile, [scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command 2>&1 | ForEach-Object { "$_" } | Out-File -Encoding utf8 $LogFile
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
}

foreach ($app in $Benchmarks.Keys) {
    $php, $schema = $Benchmarks[$app]
    $database = "c3_e3_$app"
    $out = Join-Path $WorkDir $app
    Write-Host "`n=== $app -> $database"

    Push-Location $Component
    try {
        $code = Invoke-Logged (Join-Path $EvidenceDir "$app.pipeline.log") { & $Python -m c3.cli run --php $php --schema $schema --output $out }
        if ($code -ne 0) { throw "C3 pipeline failed for $app" }
    } finally { Pop-Location }

    $env:MYSQL_PWD = $env:C3_DB_PASSWORD
    try {
        & $MySql -u c3_tester -h 127.0.0.1 -e "DROP DATABASE IF EXISTS $database; CREATE DATABASE $database CHARACTER SET utf8mb4;"
        if ($LASTEXITCODE -ne 0) { throw "could not reset $database" }
    } finally { Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue }

    $report = Join-Path $EvidenceDir "$app.json"
    $null = Invoke-Logged (Join-Path $EvidenceDir "$app.roundtrip.log") { & $Python (Join-Path $Root "scripts\alembic_roundtrip.py") $out --url "mysql+pymysql://c3_tester@127.0.0.1/$database" --report $report }
    $verdict = (Get-Content -Raw $report | ConvertFrom-Json).verdict
    Write-Host "$app : $verdict"
    $Results += "$app : $verdict"
}

@("# E3 Alembic round trip on MySQL", "# date: $(Get-Date -Format o)", "# git HEAD: $Head") + $Results |
    Set-Content -Encoding utf8 (Join-Path $EvidenceDir "summary.txt")

Write-Host "`nReports in $EvidenceDir. Paste summary.txt (and any FAIL report) back to Claude."
