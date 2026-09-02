#!/bin/bash
# Pre-US-session check: wallet tier, bot state, observers, multiplier in code.
set -euo pipefail
cd /root/btc-5m-bot
source venv/bin/activate
export $(grep -v '^#' .env | xargs) 2>/dev/null || true

echo "=== V1 preflight $(date -u '+%Y-%m-%d %H:%M UTC') ==="
python3 <<'PY'
import json
from pathlib import Path
from package2_strategy import (
    v1_stake_multiplier,
    v1_stake_plan_snapshot,
    v1_stake_startup_banner,
    v1_combined_daily_loss_limit_for_wallet,
    v1_combined_only_daily_stop,
    load_config,
)
from polymarket_wallet import cached_wallet_cash

wallet = float(cached_wallet_cash() or 0)
vm = v1_stake_multiplier()
plan = v1_stake_plan_snapshot(wallet=wallet)
ctrl = json.loads(Path("logs/bot_control.json").read_text())
cfg = load_config()

print(f"Wallet: ${wallet:,.2f}")
print(f"Stake tier: ×{vm:g}")
for line in v1_stake_startup_banner():
    print(line)
print(f"BTC paused: {ctrl.get('btc_paused')} · ETH paused: {ctrl.get('eth_paused')}")
print(f"BTC fav-only: {cfg.get('package2',{}).get('favorite_only')}")
print(f"Daily stop: -${v1_combined_daily_loss_limit_for_wallet():.0f} combined only={v1_combined_only_daily_stop()}")
ok = vm > 1.0 and not ctrl.get("btc_paused") and not ctrl.get("eth_paused")
print("READY" if ok else "NOT READY — fix issues above")
PY

echo ""
echo "=== Observers ==="
for name in btc eth; do
  if [ "$name" = btc ]; then
    pat="/root/btc-5m-bot/venv/bin/python /root/btc-5m-bot/observer.py"
  else
    pat="/root/btc-5m-bot/venv/bin/python /root/btc-5m-bot/eth_observer.py"
  fi
  pid=$(pgrep -f "$pat" | head -1 || true)
  if [ -n "$pid" ]; then
    echo "$name: PID $pid · started $(ps -p "$pid" -o lstart= 2>/dev/null || echo '?')"
  else
    echo "$name: NOT RUNNING"
  fi
done
systemctl is-active btc-5m-observer.service eth-5m-observer.service 2>/dev/null || true

echo ""
echo "=== Multiplier in evaluate_package2 ==="
grep -q "V1 wallet tier" package2_strategy.py && echo "Code OK: strategy_reason includes wallet tier" || echo "MISSING wallet tier in code"
