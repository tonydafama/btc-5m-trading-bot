#!/usr/bin/env python3
"""Start ETH 5m LIVE session — variant A (ETH_MIN_MOVE_USD=1.0)."""

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
    ETH_LIVE_SESSION_FILE,
    ETH_LIVE_TRADE_LOG_FILE,
    ETH_TRADE_LOG_FILE,
    LOG_DIR,
)
from eth_paper_engine import ETH_PAPER_SESSION_FILE, settle_stale_eth_trades
from eth_strategy_rules import eth_rules_preview
from new_strategy import fetch_wallet_cash

HKT = ZoneInfo("Asia/Hong_Kong")
ENV_FILE = ROOT / ".env"
BACKUP_ROOT = Path("/root/backups")


def _set_env(key: str, value: str) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n")


def main() -> None:
    settled = settle_stale_eth_trades()
    utc = datetime.now(timezone.utc)
    hkt = utc.astimezone(HKT)
    session_id = f"eth-live-{utc.strftime('%Y%m%d-%H%M%S')}"
    archive_dir = BACKUP_ROOT / f"eth-live-start-{utc.strftime('%Y%m%d_%H%M%S')}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    if ETH_TRADE_LOG_FILE.exists():
        shutil.copy2(ETH_TRADE_LOG_FILE, archive_dir / "trade_log_eth_paper.json")
    if ETH_LIVE_TRADE_LOG_FILE.exists():
        shutil.copy2(ETH_LIVE_TRADE_LOG_FILE, archive_dir / "trade_log_eth_live_prev.json")
    if ETH_PAPER_SESSION_FILE.exists():
        shutil.copy2(ETH_PAPER_SESSION_FILE, archive_dir / "eth_paper_session.json")
    if ETH_LIVE_SESSION_FILE.exists():
        shutil.copy2(ETH_LIVE_SESSION_FILE, archive_dir / "eth_live_session_prev.json")

    wallet = round(fetch_wallet_cash(live=True), 2)
    ETH_LIVE_TRADE_LOG_FILE.write_text("[]\n")

    rules = eth_rules_preview()
    session = {
        "session_id": session_id,
        "started_at_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "started_at_hkt": hkt.strftime("%Y-%m-%d %H:%M:%S HKT"),
        "strategy": "PACKAGE2+",
        "trading_mode": "live",
        "variant": "A",
        "ETH_MIN_MOVE_USD": 1.0,
        "starting_wallet_usdc": wallet,
        "starting_bankroll": wallet,
        "live_log_path": str(ETH_LIVE_TRADE_LOG_FILE),
        "paper_log_path": str(ETH_TRADE_LOG_FILE),
        "archive_path": str(archive_dir),
        "stale_settled_before_start": settled,
        "eth_params": rules,
        "note": (
            "ETH live variant A: eth_move≥$1.0 · shares Package 2+ with BTC "
            "(Fav $12 cap 0.85 · Dog $20 · US hours · ETH daily loss limit from .env)"
        ),
    }
    ETH_LIVE_SESSION_FILE.write_text(json.dumps(session, indent=2) + "\n")

    _set_env("ETH_TRADING_MODE", "live")
    _set_env("ETH_MIN_MOVE_USD", "1.0")

    print(json.dumps({
        "ok": True,
        "session_id": session_id,
        "variant": "A",
        "ETH_MIN_MOVE_USD": 1.0,
        "starting_wallet_usdc": wallet,
        "archive_path": str(archive_dir),
        "eth_trading_mode": "live",
        "next": "sudo systemctl restart eth-5m-observer",
    }, indent=2))


if __name__ == "__main__":
    main()
