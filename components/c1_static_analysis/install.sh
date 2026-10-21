#!/usr/bin/env sh
# Component 1 (Backend & Auth migration) - one-command setup for macOS/Linux. Safe to run again.
set -e
cd "$(dirname "$0")"
echo "  BACKEND & AUTH MIGRATION - setup"
PY=$(command -v python3 || command -v python) || { echo "  [X] Python 3.10+ not found"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "  [X] Python 3.10+ needed"; exit 1; }
echo "  [OK] Python $("$PY" -c 'import platform; print(platform.python_version())')"
command -v php >/dev/null && echo "  [OK] PHP found" || echo "  [!!] PHP not found: the 'Parse PHP' step will fail until it is installed"
if [ ! -f vendor/autoload.php ]; then
  if command -v composer >/dev/null; then composer install --no-interaction --quiet && echo "  [OK] PHP parser library installed"
  else echo "  [!!] Composer not found and vendor/ is missing"; fi
else echo "  [OK] PHP parser library already installed"; fi
"$PY" -m pip install --disable-pip-version-check -q -e ".[backend,dev]" || "$PY" -m pip install --disable-pip-version-check -q --break-system-packages -e ".[backend,dev]"
"$PY" -m c1 --help >/dev/null && echo "  [OK] Installed. From any folder: python -m c1 run <old-php-app> --out <results> --title MyApp"
