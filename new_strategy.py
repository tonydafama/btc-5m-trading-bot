"""NEW tiered-ask strategy — dynamic wallet bankroll, labels, PnL."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path("/root/btc-5m-bot")
CONFIG_PATH = ROOT / "logs" / "strategy_config.json"
HKT = ZoneInfo("Asia/Hong_Kong")

load_dotenv(ROOT / ".env")

OLD_MAX_ASK = 0.88
OLD_BET = 5.0


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def tier_percentages() -> dict[str, float]:
    cfg = load_config()
    pcts = cfg.get("bet_tiers_pct") or {}
    return {
        "1x": float(pcts.get("1x", 0.10)),
        "1.5x": float(pcts.get("1.5x", 0.15)),
        "3x": float(pcts.get("3x", 0.30)),
    }


def tier_stakes(bankroll: float) -> dict[str, float]:
    """Dynamic stake amounts from current bankroll and tier percentages."""
    pcts = tier_percentages()
    br = max(0.0, float(bankroll))
    return {
        "1x": round(br * pcts["1x"], 2),
        "1.5x": round(br * pcts["1.5x"], 2),
        "3x": round(br * pcts["3x"], 2),
    }


def fetch_wallet_cash(*, live: bool = True) -> float:
    try:
        from polymarket_wallet import cached_wallet_cash, fetch_wallet_status

        if not live:
            return cached_wallet_cash()
        return float(fetch_wallet_status().get("cash_usdc") or 0)
    except Exception:
        return 0.0


def trading_mode() -> str:
    return os.getenv("TRADING_MODE", "paper").lower()


def effective_bankroll(
    *,
    open_stakes: float = 0.0,
    paper_pnl: float = 0.0,
    live_wallet: bool = True,
) -> dict[str, float]:
    """Live: wallet only. Paper: wallet + simulated PnL."""
    wallet = fetch_wallet_cash(live=live_wallet)
    mode = trading_mode()
    if mode == "live":
        available = max(0.0, round(wallet - open_stakes, 2))
    else:
        available = max(0.0, round(wallet + paper_pnl - open_stakes, 2))
    return {
        "wallet_cash": round(wallet, 2),
        "paper_pnl": round(paper_pnl, 2),
        "open_stakes": round(open_stakes, 2),
        "bankroll": available,
        "trading_mode": mode,
    }


def _parse_switched_at_hkt(raw: str) -> datetime:
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=HKT)
        except ValueError:
            continue
    return datetime.strptime("2026-06-14 17:58", "%Y-%m-%d %H:%M").replace(tzinfo=HKT)


def strategy_started(dt: datetime | None = None) -> bool:
    cfg = load_config()
    switched = cfg.get("switched_at_hkt", "2026-06-14 17:58")
    start = _parse_switched_at_hkt(switched)
    now = dt or datetime.now(HKT)
    if now.tzinfo is None:
        now = now.replace(tzinfo=HKT)
    return now >= start


def now_hkt_str() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S")


def evaluate_ask_tier(ask: float, bankroll: float) -> dict[str, Any]:
    """Enter/skip from ask + dynamic stake sized to bankroll."""
    cfg = load_config()
    min_stake = float(cfg.get("min_stake_usd", 1.0))
    stakes = tier_stakes(bankroll)
    br_label = f"${bankroll:.2f}"

    if ask > 0.60:
        return {
            "entered": False,
            "stake": 0.0,
            "multiplier": "0x",
            "bankroll_used": round(bankroll, 2),
            "strategy_reason": f"Skipped: ask {ask:.2f} > 0.60 max ask",
        }

    if ask > 0.40:
        stake = stakes["1x"]
        if stake < min_stake:
            return _skip_min_stake(ask, stake, bankroll, min_stake)
        return {
            "entered": True,
            "stake": stake,
            "multiplier": "1x",
            "bankroll_used": round(bankroll, 2),
            "strategy_reason": (
                f"NEW strategy: ask {ask:.2f} within 0.40–0.60 → 1x "
                f"({tier_percentages()['1x']*100:.0f}% of {br_label}) bet ${stake:.2f}"
            ),
        }

    if ask > 0.30:
        stake = stakes["1.5x"]
        if stake < min_stake:
            return _skip_min_stake(ask, stake, bankroll, min_stake)
        return {
            "entered": True,
            "stake": stake,
            "multiplier": "1.5x",
            "bankroll_used": round(bankroll, 2),
            "strategy_reason": (
                f"NEW strategy: ask {ask:.2f} within 0.30–0.40 → 1.5x "
                f"({tier_percentages()['1.5x']*100:.0f}% of {br_label}) bet ${stake:.2f}"
            ),
        }

    stake = stakes["3x"]
    if stake < min_stake:
        return _skip_min_stake(ask, stake, bankroll, min_stake)
    return {
        "entered": True,
        "stake": stake,
        "multiplier": "3x",
        "bankroll_used": round(bankroll, 2),
        "strategy_reason": (
            f"NEW strategy: ask {ask:.2f} <= 0.30 → 3x "
            f"({tier_percentages()['3x']*100:.0f}% of {br_label}) bet ${stake:.2f}"
        ),
    }


def _skip_min_stake(ask: float, stake: float, bankroll: float, min_stake: float) -> dict[str, Any]:
    return {
        "entered": False,
        "stake": 0.0,
        "multiplier": "0x",
        "bankroll_used": round(bankroll, 2),
        "strategy_reason": (
            f"Skipped: dynamic stake ${stake:.2f} < min ${min_stake:.2f} "
            f"(bankroll ${bankroll:.2f})"
        ),
    }


def calc_win_pnl(stake: float, ask: float) -> float:
    if ask <= 0:
        return 0.0
    return round(stake * (1.0 - ask) / ask, 2)


def calc_loss_pnl(stake: float) -> float:
    return round(-stake, 2)


def side_to_contract(side: str) -> str:
    return "UP" if side.lower() == "up" else "DOWN"


def bought_label(contract: str, ask: float | None, entered: bool) -> str:
    if not entered or contract == "NONE":
        return "⚪ SKIPPED"
    emoji = "🟢" if contract == "UP" else "🔵"
    return f"{emoji} Bought {contract} @ {ask:.2f}"


def resolved_label(actual: str, bought: str) -> str:
    if actual in ("PENDING", "NONE", ""):
        return "Pending"
    actual_u = actual.upper()
    bought_u = bought.upper()
    match = actual_u == bought_u
    emoji = "🟢" if match else "🔴"
    return f"{emoji} Actual: {actual_u}"


def trade_result(resolved: str, bought: str, entered: bool) -> str:
    if not entered or bought == "NONE":
        return "SKIPPED"
    if resolved in ("PENDING", "NONE", ""):
        return "PENDING"
    if resolved.upper() == bought.upper():
        return "WIN"
    return "LOSS"


def simulate_old_strategy(
    *,
    ask: float | None,
    timing_ok: bool,
    resolved_outcome: str = "PENDING",
    bought_contract: str = "NONE",
) -> dict[str, Any]:
    would_enter = bool(
        timing_ok and ask is not None and ask > 0 and ask <= OLD_MAX_ASK
    )
    sim = {"would_enter": would_enter, "bet_size": OLD_BET if would_enter else 0.0, "pnl": 0.0}
    if not would_enter or resolved_outcome in ("PENDING", "NONE", ""):
        return sim
    contract = bought_contract if bought_contract != "NONE" else side_to_contract("up")
    won = resolved_outcome.upper() == contract
    if won and ask:
        sim["pnl"] = calc_win_pnl(OLD_BET, ask)
    elif would_enter:
        sim["pnl"] = calc_loss_pnl(OLD_BET)
    return sim


def simulate_old_for_side(
    ask: float | None,
    timing_ok: bool,
    side: str,
    resolved_outcome: str,
) -> dict[str, Any]:
    contract = side_to_contract(side) if side else "NONE"
    return simulate_old_strategy(
        ask=ask,
        timing_ok=timing_ok,
        resolved_outcome=resolved_outcome,
        bought_contract=contract if timing_ok and ask and ask <= OLD_MAX_ASK else "NONE",
    )


def dynamic_tiers_preview(bankroll: float | None = None) -> dict[str, Any]:
    br = bankroll if bankroll is not None else effective_bankroll()["bankroll"]
    stakes = tier_stakes(br)
    pcts = tier_percentages()
    return {
        "bankroll": round(br, 2),
        "dynamic": True,
        "percentages": {k: round(v * 100, 1) for k, v in pcts.items()},
        "stakes": stakes,
        "examples": [
            {"ask_range": "> 0.60", "multiplier": "0x", "stake": 0.0},
            {"ask_range": "0.40 – 0.60", "multiplier": "1x", "stake": stakes["1x"]},
            {"ask_range": "0.30 – 0.40", "multiplier": "1.5x", "stake": stakes["1.5x"]},
            {"ask_range": "≤ 0.30", "multiplier": "3x", "stake": stakes["3x"]},
        ],
    }
