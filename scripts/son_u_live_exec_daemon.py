#!/usr/bin/env python3
"""Son U · 終極5m single-leg 5m LIVE FOK executor (real CLOB).

Must run under btc-son-btc-5m-ultimate-live-exec.service with:
  config/sonu_live_exec.env → DRY_RUN=0 REAL_MONEY=1 TRADING_MODE=live DUAL_LEG_LIVE_DRY_RUN=0
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path("/root/btc-5m-bot")
sys.path.insert(0, str(ROOT))

# Must be set before son_5m_paper_daemon import side-effects
os.environ["SONU_LIVE_EXEC"] = "1"
os.environ.setdefault("SON_LAUNCH", "1")


def _preflight() -> None:
    from scripts.dual_leg_live_daemon import _wallet_cash  # type: ignore

    for key, want in (
        ("REAL_MONEY", ("1", "true", "yes", "on")),
        ("DRY_RUN", ("0", "false", "no", "off")),
        ("TRADING_MODE", ("live",)),
        ("DUAL_LEG_LIVE_DRY_RUN", ("0", "false", "no", "off")),
    ):
        val = str(os.environ.get(key, "")).strip().lower()
        if val not in want:
            raise SystemExit(f"REFUSING: {key}={os.environ.get(key)!r} (need live-safe value)")

    cash = _wallet_cash()
    if cash is None or float(cash) < 100.0:
        raise SystemExit(f"REFUSING: wallet cash {cash!r} < $100")

    # CLOB stack import smoke (no order)
    import btc_clob  # noqa: F401

    print(
        f"[son-u-live] preflight OK wallet=${float(cash):.2f} "
        f"REAL_MONEY=1 DRY_RUN=0 TRADING_MODE=live",
        flush=True,
    )


def main() -> int:
    _preflight()
    son_id = os.environ.get("SON_ID", "son-btc-5m-ultimate-live").strip()
    sys.argv = ["son_u_live_exec_daemon.py", "--son-id", son_id]
    from son_5m_paper_daemon import main as run_main

    return int(run_main())


if __name__ == "__main__":
    raise SystemExit(main())
