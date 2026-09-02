#!/usr/bin/env python3
"""Build US-session / tier backtest research from live logs or an archive folder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest_research import (  # noqa: E402
    build_session_research,
    build_session_research_from_paths,
    refresh_live_session_research,
    sync_backtest_tape_from_logs,
)
from historical_archive import archive_pre_capital, list_archives  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Session/tier backtest for US-hours pause research")
    p.add_argument("--archive", help="Archive id under logs/backtest_archive/ (default: live logs)")
    p.add_argument("--sync-tape", action="store_true", help="Backfill backtest_tape.jsonl from all logs")
    p.add_argument("--snapshot", action="store_true", help="Create full backtest archive snapshot now")
    p.add_argument("--list", action="store_true", help="List archives")
    args = p.parse_args()

    if args.list:
        print(json.dumps(list_archives(), indent=2))
        return

    if args.sync_tape:
        print(json.dumps(sync_backtest_tape_from_logs(), indent=2))

    if args.snapshot:
        entry = archive_pre_capital(reason="manual backtest snapshot")
        print(json.dumps(entry, indent=2))
        return

    if args.archive:
        adir = ROOT / "logs" / "backtest_archive" / args.archive
        research = build_session_research_from_paths(
            adir / "trade_log_live.json",
            adir / "trade_log_eth_live.json",
            source=args.archive,
        )
    else:
        sync_backtest_tape_from_logs()
        research = refresh_live_session_research()

    out = ROOT / "logs" / "session_research_live.json"
    if args.archive:
        out = ROOT / "logs" / "backtest_archive" / args.archive / "session_research.json"
        out.write_text(json.dumps(research, indent=2) + "\n")
    print(json.dumps(research, indent=2))


if __name__ == "__main__":
    main()
