"""Manual start/pause flags for live observers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot_common import LOG_DIR

HKT = ZoneInfo("Asia/Hong_Kong")
BOT_CONTROL_FILE = LOG_DIR / "bot_control.json"

_DEFAULT: dict[str, Any] = {
    "btc_paused": True,
    "eth_paused": True,
    "alpha_max_paused": False,
    "daily_stop_releases": {},
    "updated_at_hkt": None,
    "note": "Paused until $1000 capital launch or manual resume from dashboard.",
}


def _now_hkt() -> str:
    return datetime.now(timezone.utc).astimezone(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def _normalize_asset(asset: str) -> str:
    asset = asset.lower()
    if asset in ("alpha", "alpha-max"):
        asset = "alpha_max"
    if asset == "v1":
        return "v1"
    if asset not in ("btc", "eth", "alpha_max"):
        raise ValueError("asset must be btc, eth, alpha_max, or v1")
    return asset


def _next_hkt_midnight_utc() -> datetime:
    now_hkt = datetime.now(timezone.utc).astimezone(HKT)
    tomorrow = (now_hkt + timedelta(days=1)).date()
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=HKT).astimezone(timezone.utc)


def _next_daily_stop_release_utc() -> datetime:
    try:
        from package2_strategy import next_v1_trading_session_reset_utc

        return next_v1_trading_session_reset_utc()
    except Exception:
        return _next_hkt_midnight_utc()


def load_bot_control() -> dict[str, Any]:
    if not BOT_CONTROL_FILE.exists():
        return dict(_DEFAULT)
    try:
        data = json.loads(BOT_CONTROL_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT)
    out = dict(_DEFAULT)
    out.update(data)
    return out


def save_bot_control(data: dict[str, Any]) -> dict[str, Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["updated_at_hkt"] = _now_hkt()
    BOT_CONTROL_FILE.write_text(json.dumps(data, indent=2) + "\n")
    return data


def is_bot_paused(asset: str) -> bool:
    asset = _normalize_asset(asset)
    key = f"{asset.lower()}_paused"
    return bool(load_bot_control().get(key, True))


def set_bot_paused(asset: str, paused: bool) -> dict[str, Any]:
    asset = _normalize_asset(asset)
    data = load_bot_control()
    data[f"{asset}_paused"] = bool(paused)
    return save_bot_control(data)


def daily_stop_released_until(asset: str) -> str | None:
    asset = _normalize_asset(asset)
    releases = load_bot_control().get("daily_stop_releases") or {}
    raw = (releases.get(asset) or {}).get("until_utc")
    if not raw:
        return None
    try:
        until = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if datetime.now(timezone.utc) >= until:
        return None
    return until.isoformat()


def daily_stop_released(asset: str) -> bool:
    return daily_stop_released_until(asset) is not None


def release_daily_stop(asset: str) -> dict[str, Any]:
    asset = _normalize_asset(asset)
    data = load_bot_control()
    releases = dict(data.get("daily_stop_releases") or {})
    until = _next_daily_stop_release_utc()
    releases[asset] = {
        "released_at_hkt": _now_hkt(),
        "until_utc": until.isoformat(),
        "note": "Manual daily loss stop release from dashboard; expires at next US session open (~21:30 HKT).",
    }
    if asset in ("btc", "eth"):
        releases["v1"] = dict(releases[asset])
    data["daily_stop_releases"] = releases
    return save_bot_control(data)


def _daily_stop_release_status(data: dict[str, Any], asset: str) -> dict[str, Any]:
    release = ((data.get("daily_stop_releases") or {}).get(asset) or {})
    until = daily_stop_released_until(asset)
    return {
        "released": until is not None,
        "released_until_utc": until,
        "released_at_hkt": release.get("released_at_hkt") if until else None,
    }


def resume_all_bots() -> dict[str, Any]:
    data = load_bot_control()
    data["btc_paused"] = False
    data["eth_paused"] = False
    data["alpha_max_paused"] = False
    data["note"] = "All bots resumed (V1 BTC/ETH + Alpha-Max)."
    return save_bot_control(data)


def bot_control_status() -> dict[str, Any]:
    data = load_bot_control()
    return {
        "btc": {
            "paused": bool(data.get("btc_paused", True)),
            "running": not bool(data.get("btc_paused", True)),
            "daily_stop": _daily_stop_release_status(data, "btc"),
        },
        "eth": {
            "paused": bool(data.get("eth_paused", True)),
            "running": not bool(data.get("eth_paused", True)),
            "daily_stop": _daily_stop_release_status(data, "eth"),
        },
        "alpha_max": {
            "paused": bool(data.get("alpha_max_paused", False)),
            "running": not bool(data.get("alpha_max_paused", False)),
            "daily_stop": _daily_stop_release_status(data, "alpha_max"),
        },
        "updated_at_hkt": data.get("updated_at_hkt"),
        "note": data.get("note"),
    }
