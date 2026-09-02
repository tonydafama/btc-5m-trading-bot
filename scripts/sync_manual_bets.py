#!/usr/bin/env python3
"""Sync manual Polymarket bets from wallet activity (cron / manual run)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from manual_bets_sync import maybe_sync_manual_bets  # noqa: E402


def main() -> int:
    force = "--force" in sys.argv
    result = maybe_sync_manual_bets(force=force)
    print(json.dumps({k: result[k] for k in result if k != "bets"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
