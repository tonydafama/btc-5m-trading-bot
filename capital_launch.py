"""Auto-reset PnL and go live when Polymarket wallet reaches capital threshold."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot_common import (
    ETH_LIVE_SESSION_FILE,
    ETH_LIVE_TRADE_LOG_FILE,
    LIVE_SESSION_FILE,
    LIVE_TRADE_LOG_FILE,
    LOG_DIR,
    NEW_STATE_FILE,
    STRATEGY_CONFIG_FILE,
)
from bot_control import resume_all_bots
from historical_archive import PRE_1000_CUTOFF_LABEL, archive_pre_capital

HKT = ZoneInfo("Asia/Hong_Kong")
ROOT = Path("/root/btc-5m-bot")
ENV_FILE = ROOT / ".env"
LAUNCH_STATE_FILE = LOG_DIR / "capital_launch_state.json"
RISK_STATE_FILE = LOG_DIR / "package2_risk_state.json"

_DEFAULT_LAUNCH: dict[str, Any] = {
    "threshold_usd": 1000.0,
    "launched": False,
    "launched_at_hkt": None,
    "launch_wallet_usdc": None,
    "btc_session_id": None,
    "eth_session_id": None,
}


def _now_ids() -> tuple[str, str, str]:
    utc = datetime.now(timezone.utc)
    hkt = utc.astimezone(HKT)
    session_id = f"live-{utc.strftime('%Y%m%d-%H%M%S')}"
    return (
        session_id,
        utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        hkt.strftime("%Y-%m-%d %H:%M:%S HKT"),
    )


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _threshold_usd() -> float:
    cfg = _load_json(STRATEGY_CONFIG_FILE, {})
    plan = cfg.get("capital_plan") or {}
    try:
        return float(plan.get("base_usd") or 1000.0)
    except (TypeError, ValueError):
        return 1000.0


def load_launch_state() -> dict[str, Any]:
    data = _load_json(LAUNCH_STATE_FILE, dict(_DEFAULT_LAUNCH))
    out = dict(_DEFAULT_LAUNCH)
    out.update(data)
    out["threshold_usd"] = _threshold_usd()
    return out


def save_launch_state(data: dict[str, Any]) -> dict[str, Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCH_STATE_FILE.write_text(json.dumps(data, indent=2) + "\n")
    return data


def _set_env(key: str, value: str) -> None:
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


def _reset_risk_state() -> None:
    RISK_STATE_FILE.write_text(
        json.dumps(
            {
                "btc": {
                    "favorite_loss_streak": 0,
                    "favorite_paused_until_utc": None,
                    "underdog_loss_streak": 0,
                    "underdog_paused_until_utc": None,
                },
                "eth": {
                    "favorite_loss_streak": 0,
                    "favorite_paused_until_utc": None,
                    "underdog_loss_streak": 0,
                    "underdog_paused_until_utc": None,
                },
            },
            indent=2,
        )
        + "\n"
    )


def _restart_observers(logger: logging.Logger | None = None) -> None:
    for svc in ("btc-5m-observer", "eth-5m-observer"):
        try:
            subprocess.run(
                ["systemctl", "restart", svc],
                check=False,
                timeout=45,
                capture_output=True,
                text=True,
            )
            if logger:
                logger.info("Restarted %s after capital launch", svc)
        except Exception as exc:
            if logger:
                logger.warning("Could not restart %s: %s", svc, exc)


def execute_capital_launch(wallet_usdc: float, *, logger: logging.Logger | None = None) -> dict[str, Any]:
    """Archive logs, reset PnL baseline, switch both bots to live, resume trading."""
    state = load_launch_state()
    if state.get("launched"):
        return {"ok": True, "already_launched": True, **state}

    wallet = round(float(wallet_usdc), 2)
    btc_session_id, started_utc, started_hkt = _now_ids()
    eth_session_id = f"eth-{btc_session_id.split('-', 1)[1]}"

    archive_entry = archive_pre_capital(
        archive_id=f"pre-1000-launch-{btc_session_id.split('-', 1)[1]}",
        reason=f"capital launch at ${wallet:.2f}",
        cutoff_label=PRE_1000_CUTOFF_LABEL,
    )
    archive_dir = Path(archive_entry["archive_path"])

    LIVE_TRADE_LOG_FILE.write_text("[]\n")
    ETH_LIVE_TRADE_LOG_FILE.write_text("[]\n")

    from manual_bets_sync import reset_manual_bets_for_capital_launch

    manual_reset = reset_manual_bets_for_capital_launch(started_hkt)

    btc_session = {
        "session_id": btc_session_id,
        "started_at_utc": started_utc,
        "started_at_hkt": started_hkt,
        "strategy": "PACKAGE2+",
        "trading_mode": "live",
        "dynamic_bankroll": False,
        "starting_wallet_usdc": wallet,
        "capital_launch": True,
        "archive_path": str(archive_dir),
        "backtest_archive_id": archive_entry.get("id"),
        "note": f"Auto capital launch at ${wallet:.2f} — PnL reset, live trading enabled. Pre-launch data archived.",
    }
    eth_session = {
        "session_id": eth_session_id,
        "started_at_utc": started_utc,
        "started_at_hkt": started_hkt,
        "strategy": "PACKAGE2+",
        "trading_mode": "live",
        "starting_wallet_usdc": wallet,
        "starting_bankroll": wallet,
        "capital_launch": True,
        "archive_path": str(archive_dir),
        "backtest_archive_id": archive_entry.get("id"),
        "note": f"Auto capital launch at ${wallet:.2f} — PnL reset, live trading enabled. Pre-launch data archived.",
    }
    LIVE_SESSION_FILE.write_text(json.dumps(btc_session, indent=2) + "\n")
    ETH_LIVE_SESSION_FILE.write_text(json.dumps(eth_session, indent=2) + "\n")

    state = _load_json(NEW_STATE_FILE, {})
    state.update(
        {
            "strategy": "PACKAGE2+",
            "dynamic_bankroll": False,
            "wallet_cash": wallet,
            "bankroll": wallet,
            "trades_entered": 0,
            "trades_skipped": 0,
            "live_session_id": btc_session_id,
        }
    )
    NEW_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
    _reset_risk_state()
    _set_env("TRADING_MODE", "live")
    _set_env("ETH_TRADING_MODE", "live")
    resume_all_bots()

    launch_state = {
        "threshold_usd": _threshold_usd(),
        "launched": True,
        "launched_at_hkt": started_hkt,
        "launch_wallet_usdc": wallet,
        "btc_session_id": btc_session_id,
        "eth_session_id": eth_session_id,
        "archive_path": str(archive_dir),
        "backtest_archive_id": archive_entry.get("id"),
        "manual_bets_archived": manual_reset.get("archived_bet_count", 0),
    }
    save_launch_state(launch_state)
    _restart_observers(logger)

    if logger:
        logger.info(
            "Capital launch complete: wallet $%.2f · BTC session %s · ETH session %s",
            wallet,
            btc_session_id,
            eth_session_id,
        )
    return {"ok": True, "launched": True, "backtest_archive": archive_entry, **launch_state}


def maybe_launch_on_capital(
    cash_usdc: float | None = None,
    *,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Detect wallet >= threshold once; reset PnL and enable live trading automatically."""
    state = load_launch_state()
    if state.get("launched"):
        return {"ok": True, "already_launched": True, **state}

    threshold = float(state.get("threshold_usd") or _threshold_usd())
    if cash_usdc is None:
        from new_strategy import fetch_wallet_cash

        cash_usdc = fetch_wallet_cash(live=True)
    cash = round(float(cash_usdc), 2)
    if cash < threshold:
        return {
            "ok": True,
            "waiting": True,
            "cash_usdc": cash,
            "threshold_usd": threshold,
            "need_usd": round(threshold - cash, 2),
        }
    return execute_capital_launch(cash, logger=logger)


def launch_status() -> dict[str, Any]:
    state = load_launch_state()
    threshold = float(state.get("threshold_usd") or _threshold_usd())
    out = {
        "threshold_usd": threshold,
        "launched": bool(state.get("launched")),
        "launched_at_hkt": state.get("launched_at_hkt"),
        "launch_wallet_usdc": state.get("launch_wallet_usdc"),
        "btc_session_id": state.get("btc_session_id"),
        "eth_session_id": state.get("eth_session_id"),
    }
    if not out["launched"]:
        try:
            from polymarket_wallet import cached_wallet_cash

            cash = cached_wallet_cash()
            if cash is not None:
                out["cash_usdc"] = round(float(cash), 2)
                out["need_usd"] = round(max(0.0, threshold - float(cash)), 2)
        except Exception:
            pass
    return out
