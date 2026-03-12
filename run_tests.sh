#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  VENV_PY="$ROOT_DIR/.venv/bin/python"
elif [[ -x "$ROOT_DIR/.venv/Scripts/python.exe" ]]; then
  VENV_PY="$ROOT_DIR/.venv/Scripts/python.exe"
else
  echo "Missing .venv Python runtime."
  echo "Run ./scripts/init_python312_venv.sh first."
  exit 1
fi

if ! "$VENV_PY" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)
PY
then
  echo ".venv must use Python 3.12.x."
  echo "Run ./scripts/init_python312_venv.sh to rebuild it."
  exit 1
fi

if ! "$VENV_PY" -m pytest --version >/dev/null 2>&1; then
  echo "pytest is not installed in .venv."
  echo "Run ./scripts/init_python312_venv.sh to install dependencies."
  exit 1
fi

exec "$VENV_PY" -m pytest -q tests "$@"
