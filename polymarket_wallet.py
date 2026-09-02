"""Polymarket wallet status — cached reads with stale fallback for fast dashboards."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

CACHE_TTL_SEC = 45
CACHE_FILE = Path(os.getenv("WALLET_CACHE_FILE", "/root/btc-5m-bot/logs/wallet_cache.json"))
STATE_FILE = Path("/root/btc-5m-bot/logs/new_strategy_state.json")
WEATHER_ROOT = Path("/root/weather-bot")
WEATHER_SITE = WEATHER_ROOT / "venv/lib/python3.12/site-packages"

_lock = threading.Lock()
_mem: dict[str, Any] = {"ts": 0.0, "data": None}


def _enrich_wallet_status(data: dict[str, Any]) -> dict[str, Any]:
    """Add human-readable ready state — api_ready=false is often low balance, not API down."""
    out = dict(data)
    cash = float(out.get("cash_usdc") or 0)
    min_bet = float(out.get("min_bet_usdc") or 5)
    allowance_ok = bool(out.get("allowance_ok", True))
    l2_ok = bool(out.get("l2_ok", True))
    err = out.get("error")

    if err and out.get("cash_usdc") is None:
        out["api_ready"] = False
        out["ready_label"] = "⚠️ API 連線失敗"
        out["ready_reason"] = "connection_error"
        out["ready_detail"] = str(err)[:160]
    elif not allowance_ok:
        out["api_ready"] = False
        out["ready_label"] = "⚠️ Allowance 未設定"
        out["ready_reason"] = "allowance"
        out["ready_detail"] = "USDC 授權不足，無法下單"
    elif cash < min_bet:
        out["api_ready"] = False
        out["ready_label"] = f"⚠️ 餘額不足 ${cash:.2f}（最低 ${min_bet:.0f}）"
        out["ready_reason"] = "low_balance"
        out["ready_detail"] = f"Polymarket API 正常；Cash 低於最低下注 ${min_bet:.0f}，Bot 無法開新倉"
    elif l2_ok:
        out["api_ready"] = True
        out["ready_label"] = "✅ API 可交易"
        out["ready_reason"] = "ok"
        out["ready_detail"] = ""
    else:
        out["api_ready"] = False
        out["ready_label"] = "⚠️ API 未就緒"
        out["ready_reason"] = "unknown"
        out["ready_detail"] = ""

    if out.get("stale") and out.get("api_ready"):
        out["ready_label"] = "✅ 可交易（快取）"
        out["ready_reason"] = "stale_cache"
    return out


def _stale_from_disk() -> dict[str, Any]:
    if CACHE_FILE.exists():
        try:
            payload = json.loads(CACHE_FILE.read_text())
            if payload.get("cash_usdc") is not None:
                out = dict(payload)
                out["stale"] = True
                out.setdefault("allowance_ok", True)
                return _enrich_wallet_status(out)
        except json.JSONDecodeError:
            pass
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            cash = float(state.get("wallet_cash") or 0)
            return _enrich_wallet_status({
                "cash_usdc": cash,
                "allowance_ok": True,
                "l2_ok": True,
                "stale": True,
                "source": "new_strategy_state",
                "min_bet_usdc": 5.0,
            })
        except json.JSONDecodeError:
            pass
    return _enrich_wallet_status({
        "cash_usdc": 0.0,
        "allowance_ok": False,
        "l2_ok": False,
        "stale": True,
        "min_bet_usdc": 5.0,
    })


def _fetch_live() -> dict[str, Any]:
    if str(WEATHER_ROOT) not in sys.path:
        sys.path.insert(0, str(WEATHER_ROOT))
    if str(WEATHER_SITE) not in sys.path:
        sys.path.insert(0, str(WEATHER_SITE))
    from polymarket_api import PolymarketClient  # noqa: WPS433

    data = PolymarketClient().fetch_wallet_status()
    data["stale"] = False
    data["cached_at"] = time.time()
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2))
    return data


def fetch_wallet_status(*, force: bool = False) -> dict[str, Any]:
    """Return wallet status; reuse in-memory cache to avoid slow duplicate API calls."""
    now = time.time()
    with _lock:
        if not force and _mem["data"] is not None and now - float(_mem["ts"]) < CACHE_TTL_SEC:
            return dict(_mem["data"])

    try:
        data = _fetch_live()
    except Exception as exc:
        data = _stale_from_disk()
        data["error"] = str(exc)

    data = _enrich_wallet_status(data)
    if data.get("cash_usdc") is not None and not data.get("error"):
        try:
            from capital_launch import maybe_launch_on_capital

            launch = maybe_launch_on_capital(float(data["cash_usdc"]))
            data["capital_launch"] = launch
        except Exception:
            pass

    with _lock:
        _mem["data"] = data
        _mem["ts"] = time.time()
    return dict(data)


def cached_wallet_cash() -> float:
    """Fast path for dashboards — never blocks on a live Polymarket round-trip."""
    return float(_stale_from_disk().get("cash_usdc") or 0)
