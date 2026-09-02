#!/usr/bin/env python3
"""Daily ops snapshot — four bots PnL, processes, execution metrics, alerts."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from ops_metrics import build_daily_ops_report, format_daily_ops_text, write_daily_ops_report


def _maybe_telegram(text: str) -> None:
    from telegram_alert import send_telegram

    if not send_telegram(text):
        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            print("telegram send failed", file=sys.stderr)


def main() -> None:
    days = int(os.getenv("OPS_REPORT_LOOKBACK_DAYS", "1"))
    path = write_daily_ops_report(days=days)
    report = json.loads(path.read_text())
    text = format_daily_ops_text(report)
    print(text)
    _maybe_telegram(text)


if __name__ == "__main__":
    main()
