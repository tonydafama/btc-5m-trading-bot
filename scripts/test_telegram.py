#!/usr/bin/env python3
"""Send a test Telegram message (daily-stop preview or custom text)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from telegram_alert import format_daily_stop_message, send_telegram, telegram_configured


def main() -> None:
    if not telegram_configured():
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        sys.exit(1)
    if len(sys.argv) > 1 and sys.argv[1] == "--preview":
        text = format_daily_stop_message(pnl=-452.0, limit=450.0)
        print(text)
        return
    text = sys.argv[1] if len(sys.argv) > 1 else "✅ BTC 5m bot — Telegram alerts connected."
    ok = send_telegram(text)
    print("sent" if ok else "failed")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
