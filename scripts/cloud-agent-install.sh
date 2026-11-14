#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO_ROOT:-/agent/repos/btc-5m-trading-bot}"
cd "$REPO"

# Cloud Agent VM: Python venv + pinned requirements (dashboard / tests / scripts).
if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "python3-venv missing; install python3-venv in the environment image" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

.venv/bin/python -c "import flask, dotenv, requests"