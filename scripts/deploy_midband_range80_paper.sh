#!/bin/bash
# Deploy ONLY the Mid-band Range80 paper unit on the droplet.
# Does not set DRY_RUN=0 or REAL_MONEY=1.
# Does not restart or unmask live units, sshd, Parent V2, newnew-A, or Son U.
set -euo pipefail

ROOT="${MIDBAND_BOT_ROOT:-/root/btc-5m-bot}"
UNIT="btc-midband-range80-paper.service"
SRC_UNIT="$ROOT/scripts/systemd-sons/$UNIT"
ENV_FILE="$ROOT/config/midband_range80_paper.env"

if [[ ! -f "$ROOT/scripts/midband_range80_paper_daemon.py" ]]; then
  echo "missing paper daemon under $ROOT" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE" >&2
  exit 1
fi
if grep -E '^(DRY_RUN=0|REAL_MONEY=1|TRADING_MODE=live|DUAL_LEG_LIVE_DRY_RUN=0)' "$ENV_FILE"; then
  echo "refuse: paper env file asks for live" >&2
  exit 1
fi

install -m 644 "$SRC_UNIT" "/etc/systemd/system/$UNIT"
# Reset this strategy's book only, then start the paper unit.
"$ROOT/venv/bin/python" "$ROOT/scripts/reset_midband_range80_paper_session.py" "$ROOT"
systemctl daemon-reload
systemctl enable --now "$UNIT"
# Dashboard only, so the new card is served. Not a live trading unit.
systemctl restart btc-5m-dashboard || true

echo "---- $UNIT ----"
systemctl is-active "$UNIT"
systemctl show "$UNIT" -p ActiveState -p UnitFileState -p Environment --no-pager
echo "---- leave other units untouched (status only) ----"
for other in dual-leg-live-v2.service btc-son-btc-5m-ultimate-live-exec.service btc-son-btc-2legs-v2-newnew-a.service; do
  echo -n "$other "
  systemctl is-active "$other" || true
done
echo "session:"
python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' \
  "$ROOT/logs/sons/midband-range80_session.json"
