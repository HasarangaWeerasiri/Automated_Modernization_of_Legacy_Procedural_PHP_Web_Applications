<#
.SYNOPSIS
  Run C3 on NEWS-BUZZ, Dignity-Dev and Fee-Rainbow into NEW output folders.

.DESCRIPTION
  Same options as the S15 evaluation runs: `python -m c3.cli run` with the
  app-only PHP copy (third-party directories excluded) and the app's schema
  dump. Each result goes to a new folder:
    output/news_buzz, output/dignity_dev_hms, output/fee_rainbow
  The script refuses to run if any target folder already exists, and it
  never writes into output/hms_kishan0725 or any other existing folder.

.EXAMPLE
  .\scripts\regenerate_outputs.ps1
#>

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Component = Join-Path $Root "components\c3_data_layer"
$Python = Join-Path $Component ".venv\Scripts\python.exe"
$Apps = Join-Path $Root "benchmarks\apps"
$Output = Join-Path $Root "output"

$Runs = [ordered]@{
    "news_buzz"       = @("$Apps\_app_only\news_buzz",       "$Apps\news_buzz\db\NEWSBUZZ.sql")
    "dignity_dev_hms" = @("$Apps\_app_only\dignity_dev_hms", "$Apps\dignity_dev_hms\127_0_0_1.sql")
    "fee_rainbow"     = @("$Apps\_app_only\fee_rainbow",     "$Apps\fee_rainbow\database\db.sql")
}

# Check every target first, so nothing is written if any one of them exists.
foreach ($app in $Runs.Keys) {
    $target = Join-Path $Output $app
    if ($app -eq "hms_kishan0725") { throw "Refusing to write into the frozen output/hms_kishan0725." }
    if (Test-Path $target) {
        throw "Target folder already exists: $target. Remove or rename it yourself; this script never overwrites."
    }
    foreach ($sourcePath in $Runs[$app]) {
        if (-not (Test-Path $sourcePath)) { throw "Missing input for ${app}: $sourcePath" }
    }
}

$env:PYTHONPATH = Join-Path $Component "src"
$env:PYTHONDONTWRITEBYTECODE = "1"

Push-Location $Component
try {
    foreach ($app in $Runs.Keys) {
        $php, $schema = $Runs[$app]
        $target = Join-Path $Output $app
        Write-Host "`n=== $app -> $target"
        & $Python -m c3.cli run --php $php --schema $schema --output $target
        if ($LASTEXITCODE -ne 0) { throw "C3 failed for $app (exit $LASTEXITCODE)" }
    }
} finally {
    Pop-Location
}

Write-Host "`nDone. New folders: output/news_buzz, output/dignity_dev_hms, output/fee_rainbow"

# ---------------------------------------------------------------------------
# Older apps, for reference only (NOT run by this script). Their output folders
# already exist (output/simple_crud, output/mixed_crud, output/hard_crud,
# output/helpdesk_legacy_holdout, and the untracked output/clinic_inventory_legacy_hard),
# so pick a NEW folder name if you ever run one of these. Run from
# components\c3_data_layer with $env:PYTHONPATH = "src".
#
# & .venv\Scripts\python.exe -m c3.cli run --php ..\..\benchmarks\apps\simple_crud\legacy --schema ..\..\benchmarks\apps\simple_crud\schema.sql --output ..\..\output\<new_folder>
# & .venv\Scripts\python.exe -m c3.cli run --php ..\..\benchmarks\apps\mixed_crud\legacy --schema ..\..\benchmarks\apps\mixed_crud\schema.sql --output ..\..\output\<new_folder>
# & .venv\Scripts\python.exe -m c3.cli run --php ..\..\benchmarks\apps\hard_crud\legacy --schema ..\..\benchmarks\apps\hard_crud\schema.sql --output ..\..\output\<new_folder>
# & .venv\Scripts\python.exe -m c3.cli run --php ..\..\benchmarks\apps\clinic_inventory_legacy_hard\legacy --schema ..\..\benchmarks\apps\clinic_inventory_legacy_hard\database\schema.sql --output ..\..\output\<new_folder>
# & .venv\Scripts\python.exe -m c3.cli run --php ..\..\benchmarks\apps\helpdesk_legacy_holdout\legacy --schema ..\..\benchmarks\apps\helpdesk_legacy_holdout\database\schema.sql --output ..\..\output\<new_folder>
# ---------------------------------------------------------------------------
