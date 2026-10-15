<#
.SYNOPSIS
  Run the C3 MySQL-dependent tests on the local MySQL 8 service and save raw logs under evidence/.

.DESCRIPTION
  Run this yourself in PowerShell from the repository root. Prerequisites:

  1. The password must be set for this terminal session only. Never put it in a file:
       $env:C3_DB_PASSWORD = Read-Host "c3_tester password"
     The tests pass it to the PHP harnesses as C3_BENCH_DB_PASSWORD themselves.

  2. The throwaway account and databases must exist. Create them once, as a MySQL admin, in the
     MySQL 8 client ("C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p):
       CREATE USER IF NOT EXISTS 'c3_tester'@'localhost' IDENTIFIED BY '<throwaway>';
       CREATE DATABASE IF NOT EXISTS c3_clinic_legacy_test   CHARACTER SET utf8mb4;
       CREATE DATABASE IF NOT EXISTS c3_clinic_migrated_test CHARACTER SET utf8mb4;
       GRANT ALL ON c3_clinic_legacy_test.*   TO 'c3_tester'@'localhost';
       GRANT ALL ON c3_clinic_migrated_test.* TO 'c3_tester'@'localhost';
     For -IncludeCrudScripts you also need c3_php_validation, c3_python_validation,
     c3_mixed_php, c3_mixed_python, c3_hard_php and c3_hard_python, set up the same way as on 2026-10-08.

  3. -ResetClinicDatabases re-imports the Clinic schema.sql + seed.sql into both Clinic databases
     (DROP + CREATE TABLE). Use it on the first run, or whenever the seed data may have changed.

  The script stops on the first failing step, and it never prints the password.

.PARAMETER RegenerateClinicOutput
  Regenerates output/clinic_inventory_legacy_hard (the untracked live folder) with the current code.
  The Clinic equivalence tests read data_access.py from there until task S4 moves them to tmp_path.

.EXAMPLE
  $env:C3_DB_PASSWORD = Read-Host "c3_tester password"
  .\scripts\run_mysql_tests.ps1 -ResetClinicDatabases -RegenerateClinicOutput
#>
param(
    [switch]$ResetClinicDatabases,
    [switch]$RegenerateClinicOutput,
    [switch]$IncludeCrudScripts
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Component = Join-Path $Root "components\c3_data_layer"
$Python = Join-Path $Component ".venv\Scripts\python.exe"
$MySql = "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"
$Clinic = Join-Path $Root "benchmarks\apps\clinic_inventory_legacy_hard"
$Stamp = Get-Date -Format "yyyy-MM-dd"
$EvidenceDir = Join-Path $Root "evidence\mysql_$Stamp"

if (-not $env:C3_DB_PASSWORD) {
    throw 'C3_DB_PASSWORD is not set. Run: $env:C3_DB_PASSWORD = Read-Host "c3_tester password"'
}

New-Item -ItemType Directory -Force $EvidenceDir | Out-Null
$env:PYTHONPATH = Join-Path $Component "src"
$env:PYTHONDONTWRITEBYTECODE = "1"

# MYSQL_PWD keeps the password off the command line; it only lives in this process.
function Invoke-MySqlFile([string]$Database, [string]$SqlFile) {
    $env:MYSQL_PWD = $env:C3_DB_PASSWORD
    try {
        Get-Content -Raw $SqlFile | & $MySql -u c3_tester -h 127.0.0.1 $Database
        if ($LASTEXITCODE -ne 0) { throw "mysql import failed: $SqlFile -> $Database" }
    } finally {
        Remove-Item Env:\MYSQL_PWD -ErrorAction SilentlyContinue
    }
}

if ($ResetClinicDatabases) {
    foreach ($db in "c3_clinic_legacy_test", "c3_clinic_migrated_test") {
        Write-Host "Resetting $db from schema.sql + seed.sql"
        Invoke-MySqlFile $db (Join-Path $Clinic "database\schema.sql")
        Invoke-MySqlFile $db (Join-Path $Clinic "database\seed.sql")
    }
}

if ($RegenerateClinicOutput) {
    Write-Host "Regenerating output/clinic_inventory_legacy_hard with current code"
    Push-Location $Component
    try {
        & $Python -m c3.cli run --php "$Clinic\legacy" --schema "$Clinic\database\schema.sql" --output (Join-Path $Root "output\clinic_inventory_legacy_hard")
        if ($LASTEXITCODE -ne 0) { throw "C3 pipeline failed" }
    } finally { Pop-Location }
}

# Environment header (no secrets) + full verbose pytest log.
$Log = Join-Path $EvidenceDir "clinic_mysql_equivalence_log.txt"
$Head = git -C $Root rev-parse HEAD
$Version = & $Python -c "import os,pymysql; c=pymysql.connect(host='127.0.0.1',user='c3_tester',password=os.environ['C3_DB_PASSWORD']); cur=c.cursor(); cur.execute('SELECT VERSION()'); print(cur.fetchone()[0])"
@(
    "# date: $(Get-Date -Format o)"
    "# git HEAD: $Head"
    "# MySQL server: $Version"
    "# command: python -m pytest tests/benchmarks/clinic_hard -v -rs -p no:cacheprovider"
) | Set-Content -Encoding utf8 $Log

Push-Location $Component
try {
    & $Python -m pytest tests/benchmarks/clinic_hard -v -rs -p no:cacheprovider 2>&1 | Tee-Object -FilePath $Log -Append
    $PytestExit = $LASTEXITCODE
    "# pytest exit code: $PytestExit" | Add-Content -Encoding utf8 $Log

    # Full suite with MySQL enabled (expected: 575 collected, no MySQL skips).
    $FullLog = Join-Path $EvidenceDir "full_suite_with_mysql_log.txt"
    "# date: $(Get-Date -Format o)`n# git HEAD: $Head`n# MySQL server: $Version" | Set-Content -Encoding utf8 $FullLog
    & $Python -m pytest -q -rs -p no:cacheprovider 2>&1 | Tee-Object -FilePath $FullLog -Append

    if ($IncludeCrudScripts) {
        $V = "tests\mysql_validation"
        foreach ($pair in @(
            @("run_legacy.php", "run_migrated.py", "compare_behavior.py"),
            @("run_mixed_legacy.php", "run_mixed_migrated.py", "compare_mixed_behavior.py"),
            @("run_hard_legacy.php", "run_hard_migrated.py", "compare_hard_behavior.py"))) {
            php "$V\$($pair[0])"; if ($LASTEXITCODE -ne 0) { throw "$($pair[0]) failed" }
            & $Python "$V\$($pair[1])"; if ($LASTEXITCODE -ne 0) { throw "$($pair[1]) failed" }
            & $Python "$V\$($pair[2])"; if ($LASTEXITCODE -ne 0) { throw "$($pair[2]) failed" }
        }
        Copy-Item "$V\*results.json", "$V\*comparison_report.json" $EvidenceDir
    }
} finally { Pop-Location }

Write-Host "`nLogs saved under $EvidenceDir. Paste the last 30 lines of each log back to Claude."
