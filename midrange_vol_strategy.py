"""先有波幅，先買中間價 — BTC 5m single-leg rules (unvalidated; paper-first)."""

from __future__ import annotations

import os
from typing import Any

STRATEGY_LABEL = "先有波幅·中間價"
STRATEGY_ID = "midrange_vol"

ASK_BAND_LO = float(os.getenv("MIDVOL_ASK_BAND_LO", "0.72"))
ASK_BAND_HI = float(os.getenv("MIDVOL_ASK_BAND_HI", "0.88"))
EXTREME_ASK = float(os.getenv("MIDVOL_EXTREME_ASK", "0.94"))
COIN_LO = float(os.getenv("MIDVOL_COIN_LO", "0.45"))
COIN_HI = float(os.getenv("MIDVOL_COIN_HI", "0.55"))
T_MIN = float(os.getenv("MIDVOL_T_MIN", "40"))
T_MAX = float(os.getenv("MIDVOL_T_MAX", "75"))
MIN_PRIOR_RANGE_USD = float(os.getenv("MIDVOL_MIN_PRIOR_RANGE_USD", "80"))
STAKE_USD = float(os.getenv("MIDVOL_STAKE_USD", "3"))


def in_band(ask: float | None) -> bool:
    return ask is not None and ASK_BAND_LO <= float(ask) <= ASK_BAND_HI


def skip_reason(up_ask: float | None, down_ask: float | None) -> str | None:
    if up_ask is not None and float(up_ask) > EXTREME_ASK:
        return "up_above_0.94"
    if down_ask is not None and float(down_ask) > EXTREME_ASK:
        return "down_above_0.94"
    if (
        up_ask is not None
        and down_ask is not None
        and COIN_LO <= float(up_ask) <= COIN_HI
        and COIN_LO <= float(down_ask) <= COIN_HI
    ):
        return "both_coinflip_zone"
    return None


def note_first_to_band(
    track: dict[str, Any],
    *,
    up_ask: float | None,
    down_ask: float | None,
    t_remaining: float,
) -> None:
    """Record whichever side first touches the 0.72–0.88 band this window."""
    if track.get("first_side"):
        return
    touched: list[tuple[str, float]] = []
    if in_band(up_ask):
        touched.append(("up", float(up_ask)))
    if in_band(down_ask):
        touched.append(("down", float(down_ask)))
    if not touched:
        return
    if len(touched) == 1:
        side, _ = touched[0]
    else:
        # Both in band same tick: prefer higher ask (favorite / middle-price lean).
        side = max(touched, key=lambda x: x[1])[0]
    track["first_side"] = side
    track["first_t"] = round(float(t_remaining), 1)


def evaluate_entry(
    *,
    t_remaining: float,
    prior_range_usd: float | None,
    up_ask: float | None,
    down_ask: float | None,
    track: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    if t_remaining < T_MIN or t_remaining > T_MAX:
        return None, "outside_entry_window"
    if prior_range_usd is None or float(prior_range_usd) < MIN_PRIOR_RANGE_USD:
        return None, "prior_5m_range_below_80"
    sk = skip_reason(up_ask, down_ask)
    if sk:
        return None, sk
    side = track.get("first_side")
    if not side:
        return None, "no_side_in_band_yet"
    ask = up_ask if side == "up" else down_ask
    if not in_band(ask):
        return None, "first_side_left_band"
    return (
        {
            "side": side,
            "ask": float(ask),
            "stake_usd": STAKE_USD,
            "strategy": STRATEGY_ID,
            "strategy_label": STRATEGY_LABEL,
            "first_t": track.get("first_t"),
            "prior_range_usd": float(prior_range_usd),
        },
        "enter",
    )


def rules_summary() -> dict[str, Any]:
    return {
        "label": STRATEGY_LABEL,
        "id": STRATEGY_ID,
        "validated": False,
        "note": "未用 10 月後數據獨立驗證；不可當已證實盈利",
        "entry_t_sec": [T_MIN, T_MAX],
        "prior_binance_5m_range_min_usd": MIN_PRIOR_RANGE_USD,
        "buy_band": [ASK_BAND_LO, ASK_BAND_HI],
        "skip_extreme_ask": EXTREME_ASK,
        "skip_coinflip_band": [COIN_LO, COIN_HI],
        "stake_usd": STAKE_USD,
        "hedge": False,
        "max_one_per_window": True,
    }
