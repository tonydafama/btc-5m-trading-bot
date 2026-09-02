"""Telegram alerts for V1 bot events (daily stop, etc.)."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from bot_common import LOG_DIR, setup_logger

ALERT_STATE_FILE = LOG_DIR / "telegram_alert_state.json"


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(text: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        url = (
            f"https://api.telegram.org/bot{token}/sendMessage?"
            + urllib.parse.urlencode({"chat_id": chat_id, "text": text[:4000]})
        )
        urllib.request.urlopen(url, timeout=15).read()
        return True
    except Exception as exc:
        setup_logger().warning("Telegram send failed: %s", exc)
        return False


def _load_alert_state() -> dict[str, Any]:
    if not ALERT_STATE_FILE.exists():
        return {}
    try:
        return json.loads(ALERT_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_alert_state(data: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ALERT_STATE_FILE.write_text(json.dumps(data, indent=2) + "\n")


def _daily_stop_already_sent(date_hkt: str) -> bool:
    state = _load_alert_state()
    return (state.get("v1_daily_stop") or {}).get("date_hkt") == date_hkt


def _mark_daily_stop_sent(date_hkt: str) -> None:
    state = _load_alert_state()
    state["v1_daily_stop"] = {
        "date_hkt": date_hkt,
        "sent_at_utc": datetime.utcnow().isoformat() + "Z",
    }
    _save_alert_state(state)


def format_daily_stop_message(*, pnl: float, limit: float) -> str:
    """Build Telegram text for V1 combined daily loss stop."""
    from package2_strategy import HKT, us_session_timing, v1_trading_session_info

    now_hkt = datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT")
    timing = us_session_timing()
    sess = v1_trading_session_info()
    lines = [
        "🛑 V1 Daily Stop Hit",
        "",
        f"Session net PnL: ${pnl:+.2f} (limit −${limit:.0f})",
        "BTC + ETH paused automatically.",
        "",
    ]
    if timing.get("in_session"):
        lines.append(
            f"US session: {timing['remaining_human']} left to trade today"
        )
        lines.append(
            f"Session ends {timing['session_end_et']} · {timing['session_end_hkt']}"
        )
    else:
        lines.append("US session: closed for today")
        lines.append(
            f"Next US session in {timing['until_next_open_human']}"
        )
        lines.append(
            f"Opens {timing['next_open_et']} · {timing['next_open_hkt']}"
        )
    lines.extend(
        [
            "",
            f"PnL resets at next US open ({sess.get('next_reset_hkt', '21:30 HKT')}). "
            "Resume manually on dashboard if you want to trade again this session.",
            f"Time: {now_hkt}",
        ]
    )
    return "\n".join(lines)


def notify_v1_daily_stop(*, pnl: float, limit: float) -> bool:
    """Send Telegram alert once per HKT day when combined daily stop triggers."""
    from package2_strategy import HKT

    if not telegram_configured():
        return False
    date_hkt = datetime.now(HKT).strftime("%Y-%m-%d")
    if _daily_stop_already_sent(date_hkt):
        return False
    text = format_daily_stop_message(pnl=pnl, limit=limit)
    if send_telegram(text):
        _mark_daily_stop_sent(date_hkt)
        return True
    return False
