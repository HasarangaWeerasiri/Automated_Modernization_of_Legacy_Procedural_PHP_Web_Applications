@echo off
REM ==========================================================================
REM  Component 1 (Backend & Auth migration) - one-click setup for Windows.
REM  Double-click this file, or run "install.bat" in cmd. Safe to run again.
REM  It: checks Python/PHP/Composer, installs the PHP parser if missing,
REM  installs the tool (pip install -e .[backend]) and puts c1-migrate on PATH.
REM ==========================================================================
setlocal
cd /d "%~dp0"

echo.
echo   ===========================================
echo     BACKEND ^& AUTH MIGRATION  -  setup
echo   ===========================================
echo.

REM ---- 1. Python ----------------------------------------------------------
where python >nul 2>nul
if errorlevel 1 goto :no_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto :old_python
for /f "delims=" %%v in ('python -c "import platform; print(platform.python_version())"') do echo   [OK] Python %%v

REM ---- 2. PHP (only the first step, reading PHP files, needs it) ----------
where php >nul 2>nul
if errorlevel 1 (
    echo   [!!] PHP not found. Install PHP 8.x and add it to PATH, or the "Parse PHP" step will fail.
) else (
    echo   [OK] PHP found
)

REM ---- 3. PHP parser library (vendor folder) ------------------------------
if exist "vendor\autoload.php" (
    echo   [OK] PHP parser library already installed
    goto :pip
)
where composer >nul 2>nul
if errorlevel 1 (
    echo   [!!] Composer not found and vendor\ is missing. Install Composer, then run this file again.
    goto :pip
)
echo   ... installing the PHP parser library with Composer
call composer install --no-interaction --quiet
if errorlevel 1 (
    echo   [!!] composer install failed. See the message above.
) else (
    echo   [OK] PHP parser library installed
)

:pip
REM ---- 4. The tool itself + what the generated backend needs -------------
echo   ... installing the tool (this can take a minute)
python -m pip install --disable-pip-version-check -q -e ".[backend,dev]"
if errorlevel 1 goto :pip_failed
echo   [OK] Tool installed: "python -m c1" now works from any folder

REM ---- 5. Put c1-migrate on PATH (skipped inside a virtual environment) ---
if defined VIRTUAL_ENV (
    echo   [OK] Virtual environment active: c1-migrate works while it is active
    goto :check
)
set "C1_SCRIPTS="
for /f "delims=" %%d in ('python tools\where_scripts.py') do set "C1_SCRIPTS=%%d"
if not defined C1_SCRIPTS (
    echo   [!!] Could not find c1-migrate.exe. Use "python -m c1" instead, it does the same.
    goto :check
)
powershell -NoProfile -Command "$d = $env:C1_SCRIPTS; $p = [Environment]::GetEnvironmentVariable('Path','User'); if (-not $p) { $p = '' }; if (($p -split ';') -contains $d) { '  [OK] c1-migrate already on PATH' } else { [Environment]::SetEnvironmentVariable('Path', (($p.TrimEnd(';') + ';' + $d).TrimStart(';')), 'User'); '  [OK] Added to your PATH: ' + $d }"

:check
REM ---- 6. Quick check -----------------------------------------------------
python -m c1 --help >nul 2>nul
if errorlevel 1 goto :check_failed
echo   [OK] Self-check passed
echo.
echo   All set. Open a NEW cmd window, then from any folder run:
echo.
echo     c1-migrate run "C:\path\to\old-php-app" --out "C:\path\to\results" --title MyApp
echo.
echo   (or the same with "python -m c1" instead of "c1-migrate")
echo.
goto :end

:no_python
echo   [X] Python was not found. Install Python 3.10 or newer from python.org
echo       and tick "Add python.exe to PATH" during setup. Then run this file again.
goto :fail
:old_python
echo   [X] Python 3.10 or newer is needed. Install a newer Python, then run this file again.
goto :fail
:pip_failed
echo   [X] pip install failed. Copy the red text above and send it to me.
goto :fail
:check_failed
echo   [X] The tool did not start. Copy any error above and send it to me.
goto :fail

:fail
echo.
echo   Setup did NOT finish.
echo.
pause
exit /b 1

:end
pause
exit /b 0
