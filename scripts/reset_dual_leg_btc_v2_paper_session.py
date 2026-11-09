#!/usr/bin/env python3
"""Archive BTC dual-leg V2 paper trades/state and start a fresh dashboard session.

ETH paper untouched. Observations jsonl kept. Live trading untouched.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
HKT = ZoneInfo("Asia/Hong_Kong")

BTC_TRADES = LOG_DIR / "dual_leg_btc_paper_trades.json"
BTC_STATE = LOG_DIR / "dual_leg_btc_paper_state.json"
SESSION_FILE = LOG_DIR / "dual_leg_paper_session.json"


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def main() -> None:
    now = datetime.now(timezone.utc).astimezone(HKT)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    prior = _read_json(SESSION_FILE, {})
    if not isinstance(prior, dict):
        prior = {}

    wallet_start = float(prior.get("paper_wallet_start_usd") or 100.0)
    unit_usd = float(prior.get("unit_usd") or 6.0)
    session_id = now.strftime(f"paper-v2-{int(wallet_start):.0f}-{stamp.replace('-', '')}")

    archive_dir = LOG_DIR / "archive" / f"btc_v2_paper_{stamp}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for path in (BTC_TRADES, BTC_STATE, SESSION_FILE):
        if path.exists():
            dest = archive_dir / path.name
            shutil.copy2(path, dest)
            copied.append(str(dest))

    BTC_TRADES.write_text("[]\n")
    BTC_STATE.write_text("{}\n")

    meta = {
        "session_id": session_id,
        "strategy": "DUAL_LEG_5M_PAPER_V2",
        "mode": "paper",
        "profile": "v2",
        "active": True,
        "dry_run": True,
        "competition": f"desk2bots_pnl_reset_{session_id}",
        "started_at_hkt": now.strftime("%Y-%m-%d %H:%M:%S HKT"),
        "paper_wallet_start_usd": wallet_start,
        "paper_wallet_usd": wallet_start,
        "paper_cash_available_usd": wallet_start,
        "starting_pnl_usd": 0.0,
        "unit_usd": unit_usd,
        "pair_lock_usd": round(unit_usd * 2, 2),
        "label_zh": prior.get("label_zh") or f"紙上 V2 · ${wallet_start:.0f} 本金（Desk PnL reset）",
        "prior_session_id": prior.get("session_id"),
        "archive_dir": str(archive_dir),
        "archived_files": copied,
        "observations_kept": True,
        "note": (
            f"PnL/display reset {now.strftime('%Y-%m-%d %H:%M:%S HKT')} · "
            f"archive {archive_dir} · observations.jsonl NOT touched"
        ),
        "updated_at_hkt": now.strftime("%Y-%m-%d %H:%M:%S HKT"),
    }
    SESSION_FILE.write_text(json.dumps(meta, indent=2) + "\n")

    print("OK BTC dual-leg V2 paper session reset")
    print(f"session_id={session_id}")
    print(f"archive_dir={archive_dir}")
    print(f"wallet_reset_usd={wallet_start}")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
