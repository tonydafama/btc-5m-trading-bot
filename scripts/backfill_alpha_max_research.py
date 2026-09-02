#!/usr/bin/env python3
"""Backfill Alpha-Max window research from observer caches and rebuild summary."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alpha_max_research import backfill_research_from_windows, build_research_report


def main() -> None:
    n = backfill_research_from_windows()
    report = build_research_report(refresh_cache=True)
    agg = report.get("research_aggregate") or {}
    go = report.get("go_live") or {}
    print(f"Backfilled {n} new window rows")
    print(f"Research total windows: {agg.get('windows_logged', 0)}")
    print(f"Go-live checks: {go.get('ready_count', 0)}/{go.get('total_checks', 0)}")
    print(f"Net PnL est (fees): ${(report.get('summary') or {}).get('net_pnl_est', 0):+.2f}")
    sizing = report.get("sizing") or {}
    print(f"Proposed sizing net est: ${sizing.get('proposed_net_pnl_est', 0):+.2f}")


if __name__ == "__main__":
    main()
