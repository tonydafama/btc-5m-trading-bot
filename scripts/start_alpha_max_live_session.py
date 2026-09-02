#!/usr/bin/env python3
"""Start Alpha-Max live session on Bot #2."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path("/root/btc-5m-bot")
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_INSTANCE", "bot2")
os.environ.setdefault("BOT_ENV_FILE", str(ROOT / ".env.bot2"))

from bot_instance import bootstrap_instance  # noqa: E402

bootstrap_instance(default_instance="bot2")

from alpha_max_live import ensure_live_session  # noqa: E402
from polymarket_wallet import fetch_wallet_status  # noqa: E402


def main() -> None:
    status = fetch_wallet_status(force=True)
    cash = float(status.get("cash_usdc") or 0)
    if not status.get("api_ready"):
        raise SystemExit(
            f"Wallet not ready: {status.get('ready_label')} — run setup_bot2_credentials.py first"
        )
    sess = ensure_live_session(starting_wallet_usdc=cash)
    print("Alpha-Max LIVE session started:")
    print(f"  session_id: {sess['session_id']}")
    print(f"  starting_wallet_usdc: ${sess['starting_wallet_usdc']:.2f}")
    print(f"  logs: {ROOT / 'logs' / 'bot2'}")


if __name__ == "__main__":
    main()
