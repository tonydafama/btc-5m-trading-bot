"""先有波幅，先買中間價 — Mid-band Range80 (BTC 5m, one-sided).

Paper decision only. This module never posts a CLOB order.

Rule (do not loosen):
- Polymarket BTC 5-minute Up/Down only.
- One trade per window, one side, no hedge, no second leg, no flip.
- Hold to official Polymarket settlement (caller).
- Consider a snapshot only when 40 <= t_remaining <= 75 seconds.
- Enter only if the previous *completed* Binance BTCUSDT 5m candle
  high-low (range_usd) is at least US$80.
- Buy the first side whose tradable ask is inside [0.80, 0.84] (override via
  MIDBAND_ASK_MIN / MIDBAND_ASK_MAX).
  If both qualify on the same snapshot, take the lower ask. Tie → up.
- Skip the window if either side's best ask is already above 0.94.
- Do not buy while both sides are still inside 0.45–0.55.
- Unit US$3. The existing min-share floor may raise the paper ticket
  so a later live FOK would not be rejected for size; the rule unit
  stays 3.
"""

from __future__ import annotations

import os
from typing import Any

from polymarket_fees import estimate_taker_fee

STRATEGY_ID = "midband-range80"
STRATEGY_NAME = "先有波幅，先買中間價"
STRATEGY_NAME_EN = "Mid-band Range80"
SESSION_PREFIX = "paper-midband-range80"

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


T_REMAINING_MIN = _env_float("MIDBAND_T_MIN", 40.0)
T_REMAINING_MAX = _env_float("MIDBAND_T_MAX", 75.0)
RANGE_MIN_USD = _env_float("MIDBAND_RANGE_MIN_USD", 80.0)
ASK_MIN = _env_float("MIDBAND_ASK_MIN", 0.80)
ASK_MAX = _env_float("MIDBAND_ASK_MAX", 0.85)
ASK_SKIP_ABOVE = _env_float("MIDBAND_ASK_SKIP_ABOVE", 0.94)
COIN_FLIP_MIN = 0.45
COIN_FLIP_MAX = 0.55
UNIT_USD = float(os.getenv("MIDBAND_UNIT_USD", "3") or "3")
# Same slip the V2 strict paper FOK reports (dual_leg v2 ask_slip).
ASK_SLIP = 0.02
# Observer tradable gates (strategy_rules defaults).
DEFAULT_MIN_DEPTH_USD = 50.0
DEFAULT_MAX_SPREAD = 0.06
DEFAULT_MIN_SHARES = 5.0

DEPOSIT_ADDRESS = "0x8081D24BAc2456e5526F9C6eF80CC9e8269A259F"
FUNDER_ADDRESS = "0x2c9c171cf9ba62187e2f4c252929894e47c8f9b3"
SIGNER_ADDRESS = "0x76D0C82EdD28C3E812a90c8699D4C44793371CF2"

TRADES_NAME = "midband-range80_trades.json"
STATUS_NAME = "midband-range80_status.json"
STATE_NAME = "midband-range80_state.json"
SESSION_NAME = "midband-range80_session.json"


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def best_ask(book: dict[str, Any] | None) -> float | None:
    if not isinstance(book, dict):
        return None
    return _f(book.get("best_ask"))


def in_entry_window(t_remaining: float) -> bool:
    t_rem = float(t_remaining)
    return T_REMAINING_MIN - 1e-9 <= t_rem <= T_REMAINING_MAX + 1e-9


def in_mid_band(ask: float) -> bool:
    return ASK_MIN - 1e-9 <= float(ask) <= ASK_MAX + 1e-9


def in_coin_flip(ask: float) -> bool:
    return COIN_FLIP_MIN - 1e-9 <= float(ask) <= COIN_FLIP_MAX + 1e-9


def ask_too_expensive(ask: float | None) -> bool:
    return ask is not None and float(ask) > ASK_SKIP_ABOVE + 1e-12


def is_tradable(
    book: dict[str, Any] | None,
    *,
    min_depth_usd: float = DEFAULT_MIN_DEPTH_USD,
    max_spread: float = DEFAULT_MAX_SPREAD,
) -> bool:
    """Tradable ask: a quote, spread within the observer cap, depth at the floor."""
    ask = best_ask(book)
    if ask is None or ask <= 0 or ask >= 1:
        return False
    if not isinstance(book, dict):
        return False
    spread = _f(book.get("spread"))
    if spread is not None and spread > float(max_spread) + 1e-12:
        return False
    depth = _f(book.get("ask_depth_usd")) or 0.0
    return depth + 1e-9 >= float(min_depth_usd)


def previous_completed_range_usd(klines: list[Any], now_ms: int) -> float | None:
    """High-low of the last Binance candle whose close time is already past.

    The newest kline is often still forming. That candle is not used.
    """
    completed: list[Any] = []
    for row in klines or []:
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        close_ms = _f(row[6]) if len(row) > 6 else None
        if close_ms is None or close_ms > float(now_ms):
            continue
        completed.append(row)
    if not completed:
        return None
    candle = completed[-1]
    high = _f(candle[2])
    low = _f(candle[3])
    if high is None or low is None:
        return None
    return round(high - low, 2)


def sized_stake(unit_usd: float, fill_price: float, min_shares: float) -> tuple[float, bool]:
    """Existing son floor: ticket at least min_shares × fill price."""
    unit = round(float(unit_usd), 2)
    if fill_price <= 0:
        return unit, False
    floor = round(float(min_shares) * float(fill_price), 2)
    use = max(unit, floor, 0.5)
    return round(use, 2), use > unit + 1e-9


def paper_fok_fill(
    book: dict[str, Any] | None,
    *,
    unit_usd: float = UNIT_USD,
    max_fill: float = ASK_MAX,
    slip: float = ASK_SLIP,
    min_depth_usd: float = DEFAULT_MIN_DEPTH_USD,
    min_shares: float | None = None,
) -> dict[str, Any]:
    """Paper FOK. Walks the ask book when paper_fill_sim is installed; else depth gate.

    Never calls a live order helper. Fill price includes the V2 slip and
    must stay at or under max_fill (the top of the mid band).
    """
    if min_shares is None:
        try:
            min_shares = float(os.getenv("POLYMARKET_MIN_ORDER_SHARES", str(DEFAULT_MIN_SHARES)))
        except (TypeError, ValueError):
            min_shares = DEFAULT_MIN_SHARES
    ask = best_ask(book)
    if ask is None:
        return {"filled": False, "no_fill_reason": "no_ask", "execution_path": "PAPER_DEPTH_GATE"}
    depth = _f((book or {}).get("ask_depth_usd")) or 0.0
    if depth + 1e-9 < float(min_depth_usd):
        return {
            "filled": False,
            "no_fill_reason": "depth_low",
            "ask_depth_usd": depth,
            "execution_path": "PAPER_DEPTH_GATE",
        }

    walk = None
    try:
        from paper_fill_sim import walk_ask_book_for_stake
    except ImportError:
        walk_ask_book_for_stake = None  # type: ignore[assignment]
    if walk_ask_book_for_stake is not None and isinstance(book, dict):
        try:
            probe = sized_stake(unit_usd, ask, float(min_shares))[0]
            walk = walk_ask_book_for_stake(book, stake_usd=probe, max_fill_price=float(max_fill))
        except TypeError:
            walk = None

    if walk:
        vwap = float(walk["fill_price"])
        reported = round(vwap + float(slip), 4)
        if reported > float(max_fill) + 1e-9:
            return {
                "filled": False,
                "no_fill_reason": f"slip_above_cap_{reported:.4f}",
                "execution_path": "PAPER_FOK_SIM",
            }
        walked_stake = float(walk.get("stake_usd") or unit_usd)
        stake, floored = sized_stake(walked_stake, reported, float(min_shares))
        shares = float(walk.get("shares") or 0)
        if floored and reported > 0:
            shares = stake / reported
        fee = estimate_taker_fee(shares, reported)
        return {
            "filled": True,
            "fill_price": reported,
            "stake": stake,
            "unit_usd": round(float(unit_usd), 2),
            "stake_floor_applied": floored,
            "shares": round(shares, 6),
            "estimated_taker_fee": fee,
            "book_levels_used": int(walk.get("levels_used") or 0),
            "execution_path": "PAPER_FOK_SIM",
        }

    reported = round(float(ask) + float(slip), 4)
    if reported > float(max_fill) + 1e-9:
        return {
            "filled": False,
            "no_fill_reason": f"slip_above_cap_{reported:.4f}",
            "execution_path": "PAPER_DEPTH_GATE",
        }
    stake, floored = sized_stake(unit_usd, reported, float(min_shares))
    shares = stake / reported if reported else 0.0
    fee = estimate_taker_fee(shares, reported)
    return {
        "filled": True,
        "fill_price": reported,
        "stake": stake,
        "unit_usd": round(float(unit_usd), 2),
        "stake_floor_applied": floored,
        "shares": round(shares, 6),
        "estimated_taker_fee": fee,
        "book_levels_used": 0,
        "execution_path": "PAPER_DEPTH_GATE",
    }


def decide_snapshot(
    *,
    t_remaining: float,
    range_usd: float | None,
    up_book: dict[str, Any] | None,
    down_book: dict[str, Any] | None,
    min_depth_usd: float = DEFAULT_MIN_DEPTH_USD,
    max_spread: float = DEFAULT_MAX_SPREAD,
) -> dict[str, Any]:
    """One snapshot. Actions: ignore, wait, skip_window, enter.

    ignore — outside 40–75s; the window is still open.
    wait — inside the window, not yet a buy.
    skip_window — this 5-minute window must not be entered.
    enter — buy `side` once. Caller must not add a second leg.
    """
    up = best_ask(up_book)
    down = best_ask(down_book)
    base = {
        "strategy": STRATEGY_ID,
        "t_remaining": round(float(t_remaining), 1),
        "range_usd": None if range_usd is None else round(float(range_usd), 2),
        "up_ask": up,
        "down_ask": down,
        "side": None,
        "ask": None,
        "hedge": False,
        "legs": 1,
    }
    if not in_entry_window(t_remaining):
        return {
            **base,
            "action": "ignore",
            "reason": f"outside_entry_window_T{float(t_remaining):.0f}",
            "skip_category": None,
        }
    if range_usd is None:
        return {
            **base,
            "action": "wait",
            "reason": "previous Binance 5m range unavailable",
            "skip_category": "range_unavailable",
        }
    if float(range_usd) + 1e-9 < RANGE_MIN_USD:
        return {
            **base,
            "action": "skip_window",
            "reason": f"skip_window: range ${float(range_usd):.2f} < ${RANGE_MIN_USD:.0f}",
            "skip_category": "range_below_80",
        }
    if ask_too_expensive(up) or ask_too_expensive(down):
        return {
            **base,
            "action": "skip_window",
            "reason": (
                f"skip_window: ask already above {ASK_SKIP_ABOVE:.2f} "
                f"(up={up} down={down})"
            ),
            "skip_category": "ask_above_094",
        }
    if up is not None and down is not None and in_coin_flip(up) and in_coin_flip(down):
        return {
            **base,
            "action": "wait",
            "reason": f"coin_flip up={up:.2f} down={down:.2f}",
            "skip_category": "coin_flip",
        }

    candidates: list[tuple[str, float]] = []
    for side, book, ask in (("up", up_book, up), ("down", down_book, down)):
        if ask is None or not in_mid_band(ask):
            continue
        if not is_tradable(book, min_depth_usd=min_depth_usd, max_spread=max_spread):
            continue
        candidates.append((side, float(ask)))
    if not candidates:
        return {
            **base,
            "action": "wait",
            "reason": f"no tradable ask inside [{ASK_MIN:.2f}, {ASK_MAX:.2f}]",
            "skip_category": "no_mid_band",
        }
    candidates.sort(key=lambda item: (item[1], 0 if item[0] == "up" else 1))
    side, ask = candidates[0]
    return {
        **base,
        "action": "enter",
        "side": side,
        "ask": ask,
        "reason": (
            f"mid-band {side} ask {ask:.2f} in [{ASK_MIN:.2f}, {ASK_MAX:.2f}] · "
            f"range ${float(range_usd):.2f} ≥ 80 · one side, no hedge"
        ),
        "skip_category": None,
    }


def assert_paper_env(env_text: str | None = None) -> None:
    """Refuse to run if this strategy's own env asks for live.

    Does not rewrite the shared droplet .env. Callers must set this
    process to paper themselves; this only rejects an explicit live ask.
    """
    if not env_text:
        return
    flags: dict[str, str] = {}
    for raw in env_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        flags[key.strip()] = value.strip().strip('"').strip("'")
    dry = flags.get("DRY_RUN", "1").lower()
    if dry in {"0", "false", "no", "off"}:
        raise RuntimeError("REFUSING: midband env DRY_RUN must stay 1")
    real = flags.get("REAL_MONEY", "0").lower()
    if real in {"1", "true", "yes", "on"}:
        raise RuntimeError("REFUSING: midband env REAL_MONEY must stay 0")
    mode = flags.get("TRADING_MODE", "paper").lower()
    if mode != "paper":
        raise RuntimeError("REFUSING: midband env TRADING_MODE must stay paper")
    dual = flags.get("DUAL_LEG_LIVE_DRY_RUN", "1").lower()
    if dual in {"0", "false", "no", "off"}:
        raise RuntimeError("REFUSING: midband env DUAL_LEG_LIVE_DRY_RUN must stay 1")


def force_paper_process_env() -> None:
    """Pin this process only. Does not write any env file."""
    os.environ["DRY_RUN"] = "1"
    os.environ["REAL_MONEY"] = "0"
    os.environ["TRADING_MODE"] = "paper"
    os.environ["DUAL_LEG_LIVE_DRY_RUN"] = "1"
    os.environ["MIDBAND_REAL_MONEY"] = "0"
    os.environ["MIDBAND_RANGE80_PAPER"] = "1"
