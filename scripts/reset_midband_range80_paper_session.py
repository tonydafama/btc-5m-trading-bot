#!/usr/bin/env python3
"""Reset ONLY the Mid-band Range80 paper session to a clean book.

Does not touch Parent V2, newnew-A, Son U, or any other log.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if len(sys.argv) > 1:
    ROOT = Path(sys.argv[1])
else:
    ROOT = Path("/root/btc-5m-bot")
    if not ROOT.is_dir():
        ROOT = Path(__file__).resolve().parents[1]

HKT = ZoneInfo("Asia/Hong_Kong")
SONS = ROOT / "logs" / "sons"
PREFIX = "midband-range80"
SESSION_PREFIX = "paper-midband-range80"


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    SONS.mkdir(parents=True, exist_ok=True)
    archive = SONS / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(HKT).strftime("%Y%m%d-%H%M%S")
    session_id = f"{SESSION_PREFIX}-{stamp}"
    for name in (
        f"{PREFIX}_trades.json",
        f"{PREFIX}_status.json",
        f"{PREFIX}_state.json",
        f"{PREFIX}_session.json",
    ):
        path = SONS / name
        if not path.is_file():
            continue
        if PREFIX not in path.name:
            raise SystemExit(f"refuse to archive unexpected file {path}")
        dest = archive / f"{path.stem}-before-{stamp}{path.suffix}"
        path.replace(dest)
        print(f"archived {path.name} -> {dest}")
    _write(
        SONS / f"{PREFIX}_session.json",
        {
            "session_id": session_id,
            "strategy": PREFIX,
            "display_name": "先有波幅，先買中間價",
            "started_at_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
            "mode": "paper",
            "dry_run": True,
            "real_money": False,
            "unit_usd": 3,
            "capital_usd": None,
            "capital_label": "待入金",
            "trade_count": 0,
            "pnl_usd": 0,
        },
    )
    _write(SONS / f"{PREFIX}_trades.json", [])
    _write(
        SONS / f"{PREFIX}_state.json",
        {"current_slug": None, "decided_slugs": [], "session_id": session_id},
    )
    _write(
        SONS / f"{PREFIX}_status.json",
        {
            "id": PREFIX,
            "display_name": "先有波幅，先買中間價",
            "status": "READY · paper",
            "status_label": "已重置 · Paper / prep · 真倉 OFF",
            "mode": "paper",
            "live_enabled": False,
            "live_on_air": False,
            "dry_run": True,
            "real_money": False,
            "DRY_RUN": "1",
            "REAL_MONEY": "0",
            "TRADING_MODE": "paper",
            "capital_usd": None,
            "capital_label": "待入金",
            "funded": False,
            "unit_usd": 3,
            "pnl": 0,
            "trade_count": 0,
            "session_id": session_id,
            "posted_live_orders": 0,
        },
    )
    print(session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
