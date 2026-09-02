"""ETH-OLD — fixed $3 stake, ask band 0.20–0.85 (paper data collection)."""

from __future__ import annotations

import os
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv("/root/btc-5m-bot/.env")

FIXED_STAKE = float(os.getenv("ETH_FIXED_STAKE", "3"))
MIN_ASK = float(os.getenv("ETH_MIN_ASK", "0.20"))
MAX_ASK = float(os.getenv("ETH_MAX_ASK", "0.85"))


def evaluate_eth_old(ask: float) -> dict[str, Any]:
    if ask <= 0:
        return _skip("Skipped: no ask")
    if ask < MIN_ASK:
        return _skip(f"Skipped: ask {ask:.2f} < min {MIN_ASK:.2f}")
    if ask > MAX_ASK:
        return _skip(f"Skipped: ask {ask:.2f} > max {MAX_ASK:.2f}")

    return {
        "entered": True,
        "stake": FIXED_STAKE,
        "multiplier": "fixed $3",
        "tier": "eth-old",
        "strategy_reason": f"ETH-OLD: ask {ask:.2f} → ${FIXED_STAKE:.0f} fixed",
    }


def _skip(reason: str) -> dict[str, Any]:
    return {
        "entered": False,
        "stake": 0.0,
        "multiplier": "0x",
        "tier": "skip",
        "strategy_reason": reason,
    }


def format_timing_skip(skip_reasons: list[str]) -> str:
    """Human-readable skip line with eth_move labels (not btc_move)."""
    return f"Skipped: timing/filter — {', '.join(skip_reasons)}"


def tiers_preview() -> dict[str, Any]:
    return {
        "strategy": "ETH-OLD",
        "dynamic": False,
        "stakes": {"fixed": FIXED_STAKE},
        "examples": [
            {"ask_range": f"> {MAX_ASK:.2f}", "multiplier": "0x", "stake": 0.0},
            {"ask_range": f"{MIN_ASK:.2f} – {MAX_ASK:.2f}", "multiplier": "fixed $3", "stake": FIXED_STAKE},
            {"ask_range": f"< {MIN_ASK:.2f}", "multiplier": "0x", "stake": 0.0},
        ],
    }
