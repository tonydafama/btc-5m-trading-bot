#!/usr/bin/env bash
# LIVE probe — separate from main bot log. Does NOT touch trade_log_full.json.
set -euo pipefail
ROOT="/root/btc-5m-bot"
cd "$ROOT"
exec ./venv/bin/python3 -u scripts/live_probe.py --once --stake "${1:-1}" "${@:2}"
