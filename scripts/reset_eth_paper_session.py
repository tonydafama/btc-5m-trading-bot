#!/usr/bin/env python3
"""Archive ETH paper logs and start a fresh Package 2+ evaluation session."""

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
    ETH_OBSERVATIONS_FILE,
    ETH_STATE_FILE,
    ETH_TRADE_LOG_FILE,
    ETH_WINDOW_STATS_FILE,
    ETH_WINDOWS_FILE,
    LOG_DIR,
)
from eth_paper_engine import ETH_PAPER_SESSION_FILE, settle_stale_eth_trades
from eth_strategy_rules import eth_rules_preview
from new_strategy import fetch_wallet_cash, now_hkt_str

HKT = ZoneInfo("Asia/Hong_Kong")
BACKUP_ROOT = Path("/root/backups")


def main() -> None:
    settled = settle_stale_eth_trades()
    prior_session = {}
    if ETH_PAPER_SESSION_FILE.exists():
        try:
            prior_session = json.loads(ETH_PAPER_SESSION_FILE.read_text())
        except json.JSONDecodeError:
            prior_session = {}

    utc = datetime.now(timezone.utc)
    hkt = utc.astimezone(HKT)
    stamp = utc.strftime("%Y%m%d_%H%M%S")
    session_id = f"eth-p2-{utc.strftime('%Y%m%d-%H%M%S')}"
    archive_dir = BACKUP_ROOT / f"eth-reset-{stamp}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    for path in (
        ETH_TRADE_LOG_FILE,
        ETH_PAPER_SESSION_FILE,
        ETH_WINDOWS_FILE,
        ETH_OBSERVATIONS_FILE,
        ETH_STATE_FILE,
        ETH_WINDOW_STATS_FILE,
    ):
        if path.exists():
            shutil.copy2(path, archive_dir / path.name)

    rules = eth_rules_preview()
    wallet = round(fetch_wallet_cash(live=True), 2)
    summary = {
        "archived_at_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "archived_at_hkt": hkt.strftime("%Y-%m-%d %H:%M:%S HKT"),
        "prior_session_id": prior_session.get("session_id"),
        "stale_settled_on_reset": settled,
        "archive_path": str(archive_dir),
        "eth_params": rules,
        "note": "Fresh ETH Package 2+ session with ETH-specific move/depth params.",
    }
    (archive_dir / "reset_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    ETH_TRADE_LOG_FILE.write_text("[]\n")
    ETH_WINDOWS_FILE.write_text("[]\n")
    ETH_OBSERVATIONS_FILE.write_text("")
    ETH_STATE_FILE.write_text("{}\n")
    ETH_WINDOW_STATS_FILE.write_text("{}\n")

    ETH_PAPER_SESSION_FILE.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "started_at_hkt": now_hkt_str(),
                "started_at_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "strategy": "PACKAGE2+",
                "mode": "paper",
                "starting_wallet_usdc": wallet,
                "starting_bankroll": wallet,
                "prior_session": prior_session.get("session_id"),
                "prior_archive": str(archive_dir),
                "eth_params": {
                    "min_eth_move_usd": rules["min_eth_move_usd"],
                    "min_depth_usd": rules["min_depth_usd"],
                    "min_delta_pct": rules["min_delta_pct"],
                    "entry_window": rules["entry_window"],
                },
                "note": "PnL baseline = Polymarket live wallet at reset",
            },
            indent=2,
        )
        + "\n"
    )

    print(json.dumps({"ok": True, "session_id": session_id, **summary}, indent=2))


if __name__ == "__main__":
    main()
