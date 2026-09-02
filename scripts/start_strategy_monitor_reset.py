#!/usr/bin/env python3
"""Archive all live data and reset V1 + Alpha-Max for Strategy Monitor competition."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path("/root/btc-5m-bot")
sys.path.insert(0, str(ROOT))

from strategy_monitor_reset import execute_strategy_monitor_reset  # noqa: E402


def main() -> None:
    result = execute_strategy_monitor_reset()
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
