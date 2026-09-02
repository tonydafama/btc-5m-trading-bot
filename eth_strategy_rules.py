"""ETH 5m entry filters — aligned with BTC timing/liquidity; eth_move label for delta USD."""

from __future__ import annotations

import os
from typing import Any, Optional

from dotenv import load_dotenv

from strategy_rules import (
    CUTOFF_T as BTC_CUTOFF_T,
    ENTRY_T_MAX as BTC_ENTRY_T_MAX,
    ENTRY_T_MIN as BTC_ENTRY_T_MIN,
    MAX_SPREAD as BTC_MAX_SPREAD,
    MIN_DELTA_PCT as BTC_MIN_DELTA_PCT,
)

load_dotenv("/root/btc-5m-bot/.env")

MIN_DEPTH_USD = float(os.getenv("ETH_MIN_DEPTH_USD", "30"))
MAX_SPREAD = float(os.getenv("ETH_MAX_SPREAD", str(BTC_MAX_SPREAD)))
MIN_DELTA_PCT = float(os.getenv("ETH_MIN_DELTA_PCT", str(BTC_MIN_DELTA_PCT)))
ENTRY_T_MAX = int(os.getenv("ETH_ENTRY_T_MAX", str(BTC_ENTRY_T_MAX)))
ENTRY_T_MIN = int(os.getenv("ETH_ENTRY_T_MIN", str(BTC_ENTRY_T_MIN)))
CUTOFF_T = int(os.getenv("ETH_CUTOFF_T", str(BTC_CUTOFF_T)))
# ETH 5m window moves ~$1–4 typical (BTC ~$30+); do not inherit MIN_BTC_MOVE_USD
MIN_ETH_MOVE_USD = float(os.getenv("ETH_MIN_MOVE_USD", "1.0"))
MIN_ETH_MOVE_DOG_USD = float(os.getenv("ETH_MIN_MOVE_DOG_USD", "0.35"))
MIN_ETH_DELTA_DOG_PCT = float(os.getenv("ETH_MIN_DELTA_DOG_PCT", "0.012"))
ETH_DOG_SKIP_MOMENTUM = os.getenv("ETH_DOG_SKIP_MOMENTUM", "1").lower() in ("1", "true", "yes")


def pick_side(delta_pct: float) -> str:
    return "up" if delta_pct >= 0 else "down"


def evaluate_eth_snapshot(
    t_remaining: float,
    delta_usd: float,
    delta_pct: float,
    up_book: dict[str, Any],
    down_book: dict[str, Any],
) -> dict[str, Any]:
    """Timing + liquidity + ETH window delta filters (same windows as BTC)."""
    side = pick_side(delta_pct)
    book = up_book if side == "up" else down_book
    ask = book.get("best_ask")
    spread = book.get("spread")
    depth = book.get("ask_depth_usd", 0)

    reasons: list[str] = []

    if t_remaining > ENTRY_T_MAX:
        reasons.append(f"too_early_T{t_remaining:.0f}")
    if t_remaining < ENTRY_T_MIN:
        reasons.append(f"past_entry_window_T{t_remaining:.0f}")
    if t_remaining <= CUTOFF_T:
        reasons.append(f"cutoff_T{t_remaining:.0f}")

    from package2_strategy import package2_config

    p2 = package2_config("eth")
    mid_lo = float(p2.get("mid_low_ask", 0.30))
    is_dog_ask = ask is not None and float(ask) <= mid_lo

    if is_dog_ask and ETH_DOG_SKIP_MOMENTUM:
        # Dog band (ETH scale): timing/liquidity only — cheap ask often prints before move
        pass
    elif is_dog_ask:
        move_ok = abs(delta_usd) >= MIN_ETH_MOVE_DOG_USD
        delta_ok = abs(delta_pct) >= MIN_ETH_DELTA_DOG_PCT
        if not (move_ok or delta_ok):
            reasons.append(
                f"eth_dog_move_${abs(delta_usd):.2f}_lt_{MIN_ETH_MOVE_DOG_USD:.2f}"
                f"_and_delta_{abs(delta_pct):.3f}_lt_{MIN_ETH_DELTA_DOG_PCT}"
            )
    else:
        if abs(delta_usd) < MIN_ETH_MOVE_USD:
            reasons.append(f"eth_move_${abs(delta_usd):.0f}_lt_{MIN_ETH_MOVE_USD:.0f}")
        if abs(delta_pct) < MIN_DELTA_PCT:
            reasons.append(f"delta_{abs(delta_pct):.3f}_lt_{MIN_DELTA_PCT}")

    if ask is None:
        reasons.append("no_ask")

    if spread is not None and spread > MAX_SPREAD:
        reasons.append(f"spread_{spread:.3f}_gt_{MAX_SPREAD}")
    if depth < MIN_DEPTH_USD:
        reasons.append(f"depth_${depth:.0f}_lt_{MIN_DEPTH_USD:.0f}")

    in_entry_window = ENTRY_T_MIN <= t_remaining <= ENTRY_T_MAX
    would_enter = in_entry_window and not reasons

    return {
        "side": side,
        "would_enter": would_enter,
        "in_entry_window": in_entry_window,
        "skip_reasons": reasons if reasons else None,
        "ask": ask,
        "spread": spread,
        "ask_depth_usd": depth,
        "t_remaining": round(t_remaining, 1),
    }


def best_entry_checkpoint(checkpoints: list[dict]) -> Optional[dict]:
    candidates = [c for c in checkpoints if c.get("in_entry_window")]
    if not candidates:
        return None
    with_ask = [c for c in candidates if c.get("ask") is not None]
    if not with_ask:
        return candidates[0]
    return min(with_ask, key=lambda c: c.get("ask") or 999)


def eth_rules_preview() -> dict[str, Any]:
    from package2_strategy import package2_config, trading_session_info

    p2 = package2_config("eth")
    sess = trading_session_info(asset="eth")
    return {
        "strategy": "PACKAGE2+",
        "entry_window": f"T-{ENTRY_T_MAX} → T-{ENTRY_T_MIN}",
        "cutoff": f"T-{CUTOFF_T}",
        "min_eth_move_usd": MIN_ETH_MOVE_USD,
        "min_eth_move_dog_usd": MIN_ETH_MOVE_DOG_USD,
        "eth_dog_skip_momentum": ETH_DOG_SKIP_MOMENTUM,
        "min_delta_pct": MIN_DELTA_PCT,
        "min_depth_usd": MIN_DEPTH_USD,
        "max_spread": MAX_SPREAD,
        "favorite_ask": p2.get("favorite_ask"),
        "mid_low_ask": p2.get("mid_low_ask"),
        "min_ask": p2.get("min_ask"),
        "stake_favorite": p2.get("stake_favorite"),
        "stake_mid": p2.get("stake_mid"),
        "stake_underdog": p2.get("stake_underdog"),
        "skip_mid": p2.get("skip_mid"),
        "us_regular_only": p2.get("us_regular_only"),
        "trading_hours_et": sess.get("trading_hours_et"),
        "trading_hours_hkt": sess.get("trading_hours_hkt"),
    }
