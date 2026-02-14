#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if command -v pytest >/dev/null 2>&1; then
  PYTEST="pytest"
elif python -m pytest --version >/dev/null 2>&1; then
  PYTEST="python -m pytest"
else
  echo "pytest not found. Install with: pip install pytest"
  exit 1
fi

$PYTEST -q tests "$@"
