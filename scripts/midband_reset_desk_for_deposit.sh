#!/usr/bin/env bash
# Archive mid-band desk ledgers and open a clean prep session (awaiting deposit).
set -euo pipefail

ROOT="${MIDBAND_BOT_ROOT:-/root/btc-5m-bot}"
LOG="$ROOT/logs/sons"
ARCH="$LOG/archive"
STAMP="$(TZ=Asia/Hong_Kong date +%Y%m%d-%H%M%S)"
TAG="deposit-prep-$STAMP"

mkdir -p "$ARCH"
for f in midband-range80_trades.json midband-range80_session.json midband-range80_status.json midband-range80_state.json; do
  if [[ -f "$LOG/$f" ]]; then
    cp -a "$LOG/$f" "$ARCH/${f%.json}-$TAG.json"
  fi
done

SESSION_ID="prep-midband-range80-$STAMP"
NOW="$(TZ=Asia/Hong_Kong date '+%Y-%m-%d %H:%M:%S HKT')"

python3 - "$LOG" "$SESSION_ID" "$NOW" <<'PY'
import json
import sys
from pathlib import Path

log_dir, session_id, now_hkt = sys.argv[1:4]
log = Path(log_dir)

session = {
    "session_id": session_id,
    "strategy": "midband-range80",
    "display_name": "先有波幅，先買中間價",
    "started_at_hkt": now_hkt,
    "mode": "prep",
    "dry_run": True,
    "real_money": False,
    "unit_usd": 3,
    "capital_usd": None,
    "section_start_capital_usd": None,
    "capital_label": "待入金",
    "trade_count": 0,
    "pnl_usd": 0,
    "note": "Profile A prepared. Awaiting deposit; finance chat will arm live.",
}
status = {
    "id": "midband-range80",
    "display_name": "先有波幅，先買中間價",
    "display_name_en": "Mid-band Range80",
    "armed": False,
    "launched": False,
    "status": "PREP · awaiting deposit",
    "status_label": "預備 · 待入金 · 真倉 OFF",
    "mode": "prep",
    "desk_mode": "prep",
    "live_enabled": False,
    "live_on_air": False,
    "live_armed": False,
    "dry_run": True,
    "real_money": False,
    "DRY_RUN": "1",
    "REAL_MONEY": "0",
    "TRADING_MODE": "paper",
    "capital_usd": None,
    "capital_label": "待入金",
    "funded": False,
    "unit_usd": 3.0,
    "pnl": 0.0,
    "pnl_usd": 0.0,
    "trade_count": 0,
    "entries": 0,
    "skips": 0,
    "samples": 0,
    "errors": 0,
    "session_id": session_id,
    "heartbeat_at_hkt": now_hkt,
    "started_at_hkt": now_hkt,
    "note": "Dashboard reset. Profile A env set. Live starts after deposit confirmed.",
}

(log / "midband-range80_trades.json").write_text("[]\n", encoding="utf-8")
for name, payload in (
    ("midband-range80_session.json", session),
    ("midband-range80_status.json", status),
    ("midband-range80_state.json", {}),
):
    (log / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

echo "Archived to $ARCH/*-$TAG.json"
echo "New session $SESSION_ID · trades cleared · capital 待入金"