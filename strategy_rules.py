"""Phase 1 entry evaluation — timing/liquidity filters; ask tiers in new_strategy."""

from __future__ import annotations

import os
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv("/root/btc-5m-bot/.env")

MIN_DEPTH_USD = float(os.getenv("MIN_DEPTH_USD", "50"))
MAX_SPREAD = float(os.getenv("MAX_SPREAD", os.getenv("SPREAD_MAX", "0.06")))
MIN_DELTA_PCT = float(os.getenv("MIN_DELTA_PCT", "0.025"))
ENTRY_T_MAX = int(os.getenv("ENTRY_T_MAX", "60"))
ENTRY_T_MIN = int(os.getenv("ENTRY_T_MIN", "22"))
CUTOFF_T = int(os.getenv("CUTOFF_T", "12"))
MIN_BTC_MOVE_USD = float(os.getenv("MIN_BTC_MOVE_USD", "12"))


def pick_side(delta_pct: float) -> str:
    return "up" if delta_pct >= 0 else "down"


def evaluate_snapshot(
    t_remaining: float,
    delta_usd: float,
    delta_pct: float,
    up_book: dict[str, Any],
    down_book: dict[str, Any],
) -> dict[str, Any]:
    """Timing + liquidity filters. Ask tier (NEW) applied in paper_engine."""
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

    if abs(delta_usd) < MIN_BTC_MOVE_USD:
        reasons.append(f"btc_move_${abs(delta_usd):.0f}_lt_{MIN_BTC_MOVE_USD:.0f}")
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
