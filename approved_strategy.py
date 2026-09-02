"""Approved v2 strategy — apply + status + session helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot_common import LOG_DIR, STRATEGY_CONFIG_FILE
from new_strategy import load_config, now_hkt_str
from package2_strategy import (
    ET,
    _parse_hm,
    in_us_regular_session,
    load_extended_hours_live,
    package2_config,
    set_btc_extended_mode,
    set_extended_hours_live,
)

ROOT = Path("/root/btc-5m-bot")
ENV_FILE = ROOT / ".env"
STATE_FILE = LOG_DIR / "approved_strategy_state.json"
VERSION = "v2-2026-06-20"

EXPECTED_ENV: dict[str, str] = {}

BASELINE_ENV: dict[str, str] = {}


def _set_env_var(key: str, value: str) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n")
    os.environ[key] = value


def is_us_weekday(now: datetime | None = None) -> bool:
    now = (now or datetime.now(ET)).astimezone(ET)
    return now.weekday() < 5


def in_core_stake_window(now: datetime | None = None) -> bool:
    """Mon–Fri 11:00–15:00 ET (within US session)."""
    if not in_us_regular_session(now):
        return False
    cfg = package2_config("btc")
    start_h, start_m = _parse_hm(str(cfg.get("core_window_start_et", "11:00")))
    end_h, end_m = _parse_hm(str(cfg.get("core_window_end_et", "15:00")))
    now = (now or datetime.now(ET)).astimezone(ET)
    cur = now.hour * 60 + now.minute
    return (start_h * 60 + start_m) <= cur < (end_h * 60 + end_m)


def stake_session_multiplier(asset: str = "btc", now: datetime | None = None) -> float:
    asset = asset.lower()
    if asset not in ("btc", "eth"):
        return 1.0
    if asset == "btc" and not _config_matches().get("all"):
        return 1.0
    if in_core_stake_window(now):
        mult = float(package2_config(asset).get("core_stake_multiplier") or 1.5)
        return mult if mult > 1.0 else 1.0
    return 1.0


def _config_matches() -> dict[str, Any]:
    cfg = load_config()
    p2 = cfg.get("package2") or {}
    eth = cfg.get("eth_package2") or {}
    ext = load_extended_hours_live()
    checks = {
        "us_weekdays_only": bool(p2.get("us_weekdays_only")),
        "core_window": (
            str(p2.get("core_window_start_et")) == "11:00"
            and str(p2.get("core_window_end_et")) == "15:00"
            and float(p2.get("core_stake_multiplier") or 0) == 1.5
        ),
        "btc_extended_dog_only": bool(ext.get("btc_enabled")) and bool(ext.get("btc_extended_dog_only")),
        "eth_extended_off": not bool(ext.get("eth_enabled")),
        "eth_underdog_only": bool(eth.get("underdog_only")),
        "env": all(os.getenv(k, "") == v for k, v in EXPECTED_ENV.items()),
    }
    checks["all"] = all(checks.values())
    return checks


def approved_strategy_status() -> dict[str, Any]:
    cfg = load_config()
    p2 = cfg.get("package2") or {}
    eth = cfg.get("eth_package2") or {}
    ext = load_extended_hours_live()
    checks = _config_matches()
    btc_fav = float(p2.get("stake_favorite") or 12)
    btc_dog = float(p2.get("stake_underdog") or 20)
    core_mult = float(p2.get("core_stake_multiplier") or 1.5)
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {
        "version": VERSION,
        "applied": bool(checks["all"]),
        "checks": checks,
        "applied_at_hkt": state.get("applied_at_hkt"),
        "disabled_at_hkt": state.get("disabled_at_hkt"),
        "btc": {
            "us_hours": "Mon–Fri 09:30–16:00 ET",
            "core_hours": "Mon–Fri 11:00–15:00 ET",
            "core_stakes": {"favorite": btc_fav * core_mult, "underdog": btc_dog * core_mult},
            "normal_stakes": {"favorite": btc_fav, "underdog": btc_dog},
            "extended": "dog-only outside US",
        },
        "eth": {
            "us_hours": "Mon–Fri 09:30–16:00 ET",
            "underdog_only": bool(eth.get("underdog_only")),
            "stake_underdog": float(eth.get("stake_underdog") or 60),
            "extended": "off",
        },
        "extended_hours": ext,
    }


def apply_approved_strategy() -> dict[str, Any]:
    """Apply approved v2 checklist to config, extended toggles, and .env."""
    cfg = load_config()
    p2 = dict(cfg.get("package2") or {})
    eth = dict(cfg.get("eth_package2") or {})
    p2.update(
        {
            "us_regular_only": True,
            "us_weekdays_only": True,
            "us_regular_start_et": "09:30",
            "us_regular_end_et": "16:00",
            "core_window_start_et": "11:00",
            "core_window_end_et": "15:00",
            "core_stake_multiplier": 1.5,
            "skip_mid": True,
        }
    )
    eth.update(
        {
            "underdog_only": True,
            "stake_favorite": float(eth.get("stake_favorite") or 120),
            "stake_underdog": float(eth.get("stake_underdog") or 60),
            "daily_loss_limit_usd": float(eth.get("daily_loss_limit_usd") or 150),
        }
    )
    cfg["package2"] = p2
    cfg["eth_package2"] = eth
    cfg["note"] = (
        "Approved v2 · BTC core 11-15 ET x1.5 · ETH dog only · Mon-Fri US · "
        "BTC ext dog"
    )
    STRATEGY_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    STRATEGY_CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")

    set_btc_extended_mode("extended_dog_only")
    set_extended_hours_live(asset="eth", enabled=False)

    for key, val in EXPECTED_ENV.items():
        _set_env_var(key, val)

    now_hkt = now_hkt_str()
    STATE_FILE.write_text(
        json.dumps(
            {
                "version": VERSION,
                "applied_at_hkt": now_hkt,
                "applied_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )

    status = approved_strategy_status()
    status["message"] = f"Approved strategy {VERSION} applied at {now_hkt}"
    return status


def disable_approved_strategy() -> dict[str, Any]:
    """Revert to baseline: no core boost, ETH fav+dog, BTC US hours only."""
    cfg = load_config()
    p2 = dict(cfg.get("package2") or {})
    eth = dict(cfg.get("eth_package2") or {})
    p2.update(
        {
            "core_window_start_et": "11:00",
            "core_window_end_et": "15:00",
            "core_stake_multiplier": 1.0,
            "us_regular_only": True,
            "us_weekdays_only": True,
            "skip_mid": True,
        }
    )
    eth["underdog_only"] = False
    cfg["package2"] = p2
    cfg["eth_package2"] = eth
    cfg["note"] = (
        "Baseline · BTC/ETH Mon-Fri US 9:30-16 ET · no core x1.5 · ETH fav+dog"
    )
    STRATEGY_CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")

    set_btc_extended_mode("us_hours")
    set_extended_hours_live(asset="eth", enabled=False)

    for key, val in BASELINE_ENV.items():
        _set_env_var(key, val)

    now_hkt = now_hkt_str()
    STATE_FILE.write_text(
        json.dumps(
            {
                "version": VERSION,
                "disabled_at_hkt": now_hkt,
                "disabled_at_utc": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )

    status = approved_strategy_status()
    status["message"] = f"Approved strategy {VERSION} disabled at {now_hkt} — baseline restored"
    return status
