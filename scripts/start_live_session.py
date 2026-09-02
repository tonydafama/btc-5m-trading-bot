#!/usr/bin/env python3
"""Archive paper log, start fresh live log, switch TRADING_MODE=live."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/root/btc-5m-bot")
sys.path.insert(0, str(ROOT))

from bot_common import (  # noqa: E402
    LIVE_SESSION_FILE,
    LIVE_TRADE_LOG_FILE,
    LOG_DIR,
    NEW_STATE_FILE,
    PAPER_TRADE_LOG_FILE,
    TRADE_LOG_FILE,
)
from new_strategy import fetch_wallet_cash  # noqa: E402

HKT = ZoneInfo("Asia/Hong_Kong")
ENV_FILE = ROOT / ".env"
BACKUP_ROOT = Path("/root/backups")


def _now_ids() -> tuple[str, str, str]:
    utc = datetime.now(timezone.utc)
    hkt = utc.astimezone(HKT)
    stamp = utc.strftime("%Y%m%d_%H%M%S")
    session_id = f"live-{utc.strftime('%Y%m%d-%H%M%S')}"
    return session_id, utc.strftime("%Y-%m-%dT%H:%M:%SZ"), hkt.strftime("%Y-%m-%d %H:%M:%S HKT")


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _ensure_paper_log() -> list:
    if PAPER_TRADE_LOG_FILE.exists():
        return _load_json(PAPER_TRADE_LOG_FILE, [])
    if TRADE_LOG_FILE.exists():
        rows = _load_json(TRADE_LOG_FILE, [])
        PAPER_TRADE_LOG_FILE.write_text(json.dumps(rows, indent=2))
        return rows
    return []


def _set_env_trading_mode(mode: str) -> None:
    lines = ENV_FILE.read_text().splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith("TRADING_MODE="):
            out.append(f"TRADING_MODE={mode}")
            found = True
        else:
            out.append(line)
    if not found:
        out.insert(2, f"TRADING_MODE={mode}")
    ENV_FILE.write_text("\n".join(out) + "\n")


def main() -> None:
    session_id, started_utc, started_hkt = _now_ids()
    archive_dir = BACKUP_ROOT / f"btc-live-start-{session_id.split('-', 1)[1]}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    paper_rows = _ensure_paper_log()
    if TRADE_LOG_FILE.exists():
        shutil.copy2(TRADE_LOG_FILE, archive_dir / "trade_log_full.json")
    shutil.copy2(PAPER_TRADE_LOG_FILE, archive_dir / "trade_log_paper.json")
    if LIVE_TRADE_LOG_FILE.exists():
        shutil.copy2(LIVE_TRADE_LOG_FILE, archive_dir / "trade_log_live_prev.json")
    if LIVE_SESSION_FILE.exists():
        shutil.copy2(LIVE_SESSION_FILE, archive_dir / "live_session_prev.json")
    if NEW_STATE_FILE.exists():
        shutil.copy2(NEW_STATE_FILE, archive_dir / "new_strategy_state_prev.json")

    wallet = round(fetch_wallet_cash(live=True), 2)
    LIVE_TRADE_LOG_FILE.write_text("[]\n")

    session = {
        "session_id": session_id,
        "started_at_utc": started_utc,
        "started_at_hkt": started_hkt,
        "strategy": "NEW",
        "trading_mode": "live",
        "dynamic_bankroll": True,
        "starting_wallet_usdc": wallet,
        "paper_records_preserved": len(paper_rows),
        "paper_log_path": str(PAPER_TRADE_LOG_FILE),
        "live_log_path": str(LIVE_TRADE_LOG_FILE),
        "archive_path": str(archive_dir),
        "note": (
            "Paper log keeps OLD strategy comparison on every window. "
            "Live log records real Polymarket orders only."
        ),
    }
    LIVE_SESSION_FILE.write_text(json.dumps(session, indent=2) + "\n")

    state = _load_json(NEW_STATE_FILE, {})
    state.update({
        "strategy": "NEW",
        "dynamic_bankroll": True,
        "wallet_cash": wallet,
        "bankroll": wallet,
        "trades_entered": 0,
        "trades_skipped": 0,
        "live_session_id": session_id,
    })
    NEW_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
    _set_env_trading_mode("live")

    print(json.dumps({
        "ok": True,
        "session_id": session_id,
        "starting_wallet_usdc": wallet,
        "paper_rows": len(paper_rows),
        "archive_path": str(archive_dir),
        "trading_mode": "live",
    }, indent=2))


if __name__ == "__main__":
    main()
