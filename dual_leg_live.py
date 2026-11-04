"""Dual-leg LIVE FOK executor path (BTC-only).

CRITICAL SAFETY
---------------
- DRY_RUN defaults to 1. When DRY_RUN=1 this module NEVER posts CLOB orders.
- Starting with DRY_RUN=0 requires explicit env AND calling force_live=True on
  execute helpers; the dry-run CLI refuses DRY_RUN=0 entirely.
- Asset filter: BTC only. ETH (and any other asset) is refused on this path.
- Paper dual-leg (dual_leg_paper.py) is undisturbed; this module is separate.

FOK construction mirrors btc_clob.place_fok_buy_chase / scripts/clob_place_order.py
(size = stake/price, OrderType.FOK, max_fill_price cap). Dry-run logs the payload
that WOULD be sent and simulates fills via paper_fill_sim.walk_ask_book_for_stake.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from polymarket_fees import estimate_taker_fee

ROOT = Path("/root/btc-5m-bot")
LOG_DIR = ROOT / "logs"
DRY_RUN_LOG = LOG_DIR / "dual_leg_live_dry_run.jsonl"
HKT = ZoneInfo("Asia/Hong_Kong")

STRATEGY_LIVE = "DUAL_LEG_5M_LIVE_FOK"


def _env_bool(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).lower() not in ("0", "false", "no")


# Default ON — never post unless explicitly disabled (dry-run CLI still refuses).
DRY_RUN = _env_bool("DUAL_LEG_LIVE_DRY_RUN", "1")


@dataclass(frozen=True)
class DualLegProfile:
    name: str
    unit_usd: float = 3.0
    max_ask: float = 0.48
    min_depth_usd: float = 3.0
    depth_mult: float = 1.0
    ask_slip: float = 0.0
    strict_fill: bool = False
    require_hedgeable: bool = False
    first_t_min: float = 60.0
    second_t_min: float = 22.0
    max_abs_delta_pct: float = 0.08
    max_opposite_ask: float = 0.65
    max_spread: float = 0.10
    allowed_regimes: tuple[str, ...] = ("volatile_chop",)
    require_binance_regime: bool = True
    bonus_watch_ask: float = 0.20

    def required_depth_usd(self) -> float:
        if self.strict_fill:
            return max(self.min_depth_usd, self.unit_usd * self.depth_mult)
        return self.min_depth_usd

    def effective_ask(self, raw_ask: float) -> float:
        if self.strict_fill:
            return float(raw_ask) + self.ask_slip
        return float(raw_ask)


PROFILES: dict[str, DualLegProfile] = {
    "v1": DualLegProfile(
        name="v1",
        unit_usd=3.0,
        max_ask=0.48,
        min_depth_usd=3.0,
        depth_mult=1.0,
        ask_slip=0.0,
        strict_fill=False,
        require_hedgeable=False,
        first_t_min=60.0,
        second_t_min=22.0,
        max_abs_delta_pct=0.08,
        max_opposite_ask=0.65,
        max_spread=0.10,
        allowed_regimes=("volatile_chop",),
    ),
    "v2": DualLegProfile(
        name="v2",
        unit_usd=3.0,
        max_ask=0.50,
        min_depth_usd=3.0,
        depth_mult=2.0,
        ask_slip=0.02,
        strict_fill=True,
        require_hedgeable=True,
        first_t_min=45.0,
        second_t_min=22.0,
        max_abs_delta_pct=0.12,
        max_opposite_ask=0.72,
        max_spread=0.12,
        allowed_regimes=("volatile_chop", "quiet_mixed"),
    ),
    # Paper dry-run only: soft opposite gate 0.70 (LIVE v2 stays 0.72).
    "v2_soft70": DualLegProfile(
        name="v2_soft70",
        unit_usd=3.0,
        max_ask=0.50,
        min_depth_usd=3.0,
        depth_mult=2.0,
        ask_slip=0.02,
        strict_fill=True,
        require_hedgeable=True,
        first_t_min=45.0,
        second_t_min=22.0,
        max_abs_delta_pct=0.12,
        max_opposite_ask=0.70,
        max_spread=0.12,
        allowed_regimes=("volatile_chop", "quiet_mixed"),
    ),
    # live 影武者 / Son F stack — PAPER twin + future ONLY go-live strategy (max_opp 0.55).
    # Opp-before≤0.50 is enforced in dual_leg_live_daemon when REQUIRE_OPP_BEFORE_LEG1=1.
    "v2_live_shadow": DualLegProfile(
        name="v2_live_shadow",
        unit_usd=6.0,
        max_ask=0.50,
        min_depth_usd=3.0,
        depth_mult=2.0,
        ask_slip=0.02,
        strict_fill=True,
        require_hedgeable=True,
        first_t_min=45.0,
        second_t_min=22.0,
        max_abs_delta_pct=0.12,
        max_opposite_ask=0.55,
        max_spread=0.12,
        allowed_regimes=("volatile_chop", "quiet_mixed"),
    ),
    # Live book from 2026-11-04: V2 gates, chop only, no clock pause, $15 per leg.
    "v3": DualLegProfile(
        name="v3",
        unit_usd=15.0,
        max_ask=0.50,
        min_depth_usd=3.0,
        depth_mult=2.0,
        ask_slip=0.02,
        strict_fill=True,
        require_hedgeable=True,
        first_t_min=45.0,
        second_t_min=22.0,
        max_abs_delta_pct=0.12,
        max_opposite_ask=0.72,
        max_spread=0.12,
        allowed_regimes=("volatile_chop",),
    ),
}


def now_hkt() -> str:
    return datetime.now(timezone.utc).astimezone(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


# Fresh first legs stop at 18:00 and start again at 00:00. An open pair still hedges.
ENTRY_PAUSE_REMARK_TC = (
    "BTC 2 legs V3：不設時段暫停，全日按市況開新倉。已開倉會自己行完。"
)
ENTRY_PAUSE_ACTIVE_REMARK_TC = (
    "而家暫停開新倉（香港時間 18:00–24:00），00:00 再開始。已開倉會自己行完。"
)


def _entry_pause_flag() -> str:
    """Process env wins. Otherwise the live exec env file. Default stays on."""
    raw = os.getenv("DUAL_LEG_LIVE_ENTRY_PAUSE")
    if raw is not None and str(raw).strip() != "":
        return str(raw)
    explicit = (os.getenv("DUAL_LEG_LIVE_ENV_FILE") or "").strip()
    candidates = [Path(explicit)] if explicit else []
    candidates.append(Path("/root/btc-5m-bot/config/v2_parent_live_exec.env"))
    for path in candidates:
        found = _env_file_value(path, "DUAL_LEG_LIVE_ENTRY_PAUSE")
        if found is not None and str(found).strip() != "":
            return str(found)
    return "1"


def new_entry_paused(now: datetime | None = None) -> bool:
    """True from 18:00 until 24:00 HKT. Set DUAL_LEG_LIVE_ENTRY_PAUSE=0 to disable."""
    if _entry_pause_flag().strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False
    moment = now or datetime.now(timezone.utc).astimezone(HKT)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=HKT)
    else:
        moment = moment.astimezone(HKT)
    return 18 <= moment.hour <= 23


def entry_pause_view(now: datetime | None = None) -> dict[str, Any]:
    active = new_entry_paused(now)
    return {
        "active": active,
        "window_hkt": "18:00-24:00",
        "resume_hkt": "00:00",
        "remark_tc": ENTRY_PAUSE_ACTIVE_REMARK_TC if active else ENTRY_PAUSE_REMARK_TC,
    }


def entry_opposite_record(snapshot: dict[str, Any], side: str) -> dict[str, Any]:
    """Quote of the other leg at the second the first leg is posted."""
    opposite = "down" if str(side) == "up" else "up"
    book = _book(snapshot, opposite)
    ask = book.get("best_ask")
    spread = book.get("spread")
    bid = None
    if ask is not None and spread is not None:
        bid = round(float(ask) - float(spread), 4)
    return {
        "side": opposite,
        "best_ask": None if ask is None else float(ask),
        "spread": None if spread is None else float(spread),
        "best_bid": bid,
        "ask_depth_usd": book.get("ask_depth_usd"),
        "t_remaining": snapshot.get("t_remaining"),
    }


def assert_btc_only(asset: str) -> str:
    a = (asset or "").strip().lower()
    if a != "btc":
        raise ValueError(
            f"dual_leg_live refuses asset={asset!r}; BTC-only live/dry-live path"
        )
    return a


def leg_taker_fee_usd(leg: dict[str, Any]) -> float:
    """Polymarket crypto taker fee for one filled leg (shares × price × fee curve)."""
    if not isinstance(leg, dict):
        return 0.0
    for key in ("estimated_taker_fee", "fee"):
        raw = leg.get(key)
        if raw not in (None, ""):
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    price = leg.get("ask")
    if price in (None, ""):
        price = leg.get("fill_price") or leg.get("price")
    try:
        px = float(price or 0)
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0:
        return 0.0
    shares = leg.get("shares")
    try:
        sh = float(shares or 0)
    except (TypeError, ValueError):
        sh = 0.0
    if sh <= 0:
        try:
            stake = float(leg.get("stake") or 0)
        except (TypeError, ValueError):
            stake = 0.0
        if stake > 0:
            sh = stake / px
    if sh <= 0:
        return 0.0
    return float(estimate_taker_fee(sh, px))


def enrich_row_leg_fees(row: dict[str, Any]) -> dict[str, Any]:
    """Attach estimated_taker_fee on each leg from fill price/shares."""
    out = dict(row)
    for key in ("first_leg", "second_leg"):
        leg = out.get(key)
        if not isinstance(leg, dict):
            continue
        leg = dict(leg)
        leg["estimated_taker_fee"] = round(leg_taker_fee_usd(leg), 4)
        out[key] = leg
    return out


def account_live_trade_pnl(row: dict[str, Any], outcome: str) -> dict[str, Any]:
    """Settle PnL: payout on winning shares minus stake and taker fees on all legs."""
    normalized = str(outcome or "").lower()
    if normalized not in ("up", "down"):
        return row
    row = enrich_row_leg_fees(dict(row))
    legs: list[dict[str, Any]] = []
    for key in ("first_leg", "second_leg"):
        leg = row.get(key)
        if isinstance(leg, dict):
            legs.append(leg)
    total_stake = sum(float(leg.get("stake") or 0) for leg in legs)
    total_fee = sum(float(leg.get("estimated_taker_fee") or 0) for leg in legs)
    winner = next(
        (leg for leg in legs if str(leg.get("side") or "").lower() == normalized),
        None,
    )
    payout = float(winner.get("shares") or 0) if winner else 0.0
    gross = payout - total_stake
    net = gross - total_fee
    row.update(
        {
            "status": "closed",
            "trade_result": "WIN" if net > 0.01 else ("BREAKEVEN" if abs(net) < 0.01 else "LOSS"),
            "outcome": normalized.upper(),
            "legs_filled": len(legs),
            "total_stake": round(total_stake, 2),
            "total_estimated_fee": round(total_fee, 4),
            "gross_pnl": round(gross, 2),
            "pnl": round(net, 2),
            "pnl_usd": round(net, 2),
            "close_reason": row.get("close_reason") or "settled_official_outcome",
        }
    )
    if not row.get("settled_at_hkt"):
        row["settled_at_hkt"] = now_hkt()
    return row


def dry_run_enabled() -> bool:
    return _env_bool("DUAL_LEG_LIVE_DRY_RUN", "1")


def refuse_live_start() -> None:
    """Hard stop for runners that must never go live."""
    if not dry_run_enabled():
        raise RuntimeError(
            "DUAL_LEG_LIVE_DRY_RUN=0 refused. This task/runner is dry-run only."
        )


@dataclass
class FokOrderIntent:
    """Payload that WOULD be sent to CLOB as FOK buy (never posted in dry-run)."""

    token_id: str
    side: str
    price: float
    stake_usd: float
    size_shares: float
    max_fill_price: float
    decision_ask: float
    order_type: str = "FOK"
    tick_size: str = "0.01"
    neg_risk: bool = False
    min_size: float = 5.0
    profile: str = ""
    slug: str = ""
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_fok_buy_intent(
    *,
    token_id: str,
    side: str,
    decision_ask: float,
    stake_usd: float,
    max_fill_price: float,
    profile: str = "",
    slug: str = "",
    tick_size: str = "0.01",
    min_size: float = 5.0,
) -> FokOrderIntent:
    """Mirror scripts/clob_place_order.py sizing: size = stake/price, FOK."""
    tick = float(tick_size or 0.01)
    price = max(tick, min(1.0 - tick, round(round(max_fill_price / tick) * tick, 4)))
    size = round(stake_usd / price, 2) if price > 0 else 0.0
    if size < min_size:
        size = float(min_size)
    return FokOrderIntent(
        token_id=str(token_id),
        side=side.lower(),
        price=price,
        stake_usd=round(stake_usd, 2),
        size_shares=size,
        max_fill_price=round(float(max_fill_price), 4),
        decision_ask=round(float(decision_ask), 4),
        profile=profile,
        slug=slug,
        dry_run=True,
        tick_size=tick_size,
        min_size=min_size,
    )


def _book(snapshot: dict[str, Any], side: str) -> dict[str, Any]:
    # Prefer nested up_book/down_book; else synthesize from flat observation fields.
    nested = snapshot.get(f"{side}_book")
    if isinstance(nested, dict) and (
        nested.get("best_ask") is not None or nested.get("asks")
    ):
        return nested
    ask = snapshot.get(f"{side}_ask")
    depth = snapshot.get(f"{side}_depth_usd")
    spread = snapshot.get(f"{side}_spread")
    out: dict[str, Any] = {
        "best_ask": float(ask) if ask is not None else None,
        "ask_depth_usd": float(depth or 0),
        "spread": float(spread) if spread is not None else None,
    }
    return out


def _fill_ok(
    book: dict[str, Any], profile: DualLegProfile, max_ask: float
) -> tuple[bool, float | None, list[str]]:
    reasons: list[str] = []
    raw_ask = book.get("best_ask")
    if raw_ask is None:
        reasons.append("no_ask")
        return False, None, reasons
    decision_ask = float(raw_ask)
    fill_ask = profile.effective_ask(decision_ask)
    depth = float(book.get("ask_depth_usd") or 0)
    need = profile.required_depth_usd()
    if depth < need:
        reasons.append("depth_low")
    if fill_ask > max_ask:
        reasons.append("fill_ask_high")
    return (not reasons), fill_ask, reasons


def simulate_fok_fill(
    book: dict[str, Any],
    *,
    profile: DualLegProfile,
    max_ask: float,
    unit_usd: float | None = None,
) -> dict[str, Any]:
    """Simulate FOK fill using walk_ask_book when levels exist; else depth gate."""
    from paper_fill_sim import walk_ask_book_for_stake

    stake = float(unit_usd if unit_usd is not None else profile.unit_usd)
    raw_ask = book.get("best_ask")
    if raw_ask is None:
        return {
            "filled": False,
            "no_fill_reason": "no_ask",
            "required_depth_usd": profile.required_depth_usd(),
            "ask_depth_usd": 0.0,
        }
    decision_ask = float(raw_ask)
    fill_cap = float(max_ask)
    # Strict slip: decision for gate uses effective; FOK max_fill is profile max_ask.
    ok, fill_ask, reasons = _fill_ok(book, profile, max_ask)
    depth = float(book.get("ask_depth_usd") or 0)
    need = profile.required_depth_usd()

    walk = walk_ask_book_for_stake(
        book, stake_usd=stake, max_fill_price=fill_cap
    )
    if walk:
        vwap = float(walk["fill_price"])
        # Apply slip conservatism for V2 reporting (paper uses +slip on decision).
        reported = (
            round(vwap + profile.ask_slip, 4) if profile.strict_fill else round(vwap, 4)
        )
        if reported > fill_cap + 1e-9:
            return {
                "filled": False,
                "no_fill_reason": f"vwap_plus_slip_{reported:.4f}_above_cap_{fill_cap:.2f}",
                "decision_ask": decision_ask,
                "sim_vwap": vwap,
                "required_depth_usd": need,
                "ask_depth_usd": depth,
                "gate_reasons": reasons,
            }
        shares = float(walk["shares"])
        fee = estimate_taker_fee(shares, reported)
        return {
            "filled": True,
            "decision_ask": round(decision_ask, 4),
            "fill_ask": reported,
            "sim_vwap": vwap,
            "stake": float(walk["stake_usd"]),
            "shares": shares,
            "estimated_taker_fee": fee,
            "required_depth_usd": need,
            "ask_depth_usd": depth,
            "book_levels_used": int(walk.get("levels_used") or 0),
            "execution_path": "DRY_FOK_SIM",
            "gate_ok": ok,
            "gate_reasons": reasons,
        }

    # Fallback: depth-only (observations lack ask ladders).
    if not ok or fill_ask is None:
        return {
            "filled": False,
            "no_fill_reason": ",".join(reasons) or "depth_or_ask_fail",
            "decision_ask": decision_ask,
            "required_depth_usd": need,
            "ask_depth_usd": depth,
            "gate_reasons": reasons,
            "execution_path": "DRY_DEPTH_GATE",
        }
    shares = stake / fill_ask
    fee = estimate_taker_fee(shares, fill_ask)
    return {
        "filled": True,
        "decision_ask": round(decision_ask, 4),
        "fill_ask": round(fill_ask, 4),
        "stake": round(stake, 2),
        "shares": round(shares, 6),
        "estimated_taker_fee": fee,
        "required_depth_usd": need,
        "ask_depth_usd": depth,
        "book_levels_used": 0,
        "execution_path": "DRY_DEPTH_GATE",
        "gate_ok": True,
        "gate_reasons": [],
    }


def first_leg_reasons(
    snapshot: dict[str, Any],
    side: str,
    regime: dict[str, Any],
    profile: DualLegProfile,
) -> list[str]:
    reasons: list[str] = []
    t_remaining = float(snapshot.get("t_remaining") or 0)
    if t_remaining < profile.first_t_min:
        reasons.append("too_late_first_leg")
    if profile.require_binance_regime and regime.get("source") != "binance_1m_klines":
        reasons.append("binance_unavailable")
    if regime.get("regime") not in profile.allowed_regimes:
        reasons.append(f"regime_{regime.get('regime', 'unknown')}")
    if abs(float(snapshot.get("delta_pct") or 0)) > profile.max_abs_delta_pct:
        reasons.append("window_move_too_directional")

    book = _book(snapshot, side)
    opposite_side = "down" if side == "up" else "up"
    opposite = _book(snapshot, opposite_side)

    ok, _, fill_reasons = _fill_ok(book, profile, profile.max_ask)
    for r in fill_reasons:
        reasons.append(f"first_{r}")
    raw = book.get("best_ask")
    if raw is not None and float(raw) > profile.max_ask and "first_fill_ask_high" not in reasons:
        reasons.append("first_ask_high")

    opposite_ask = opposite.get("best_ask")
    if opposite_ask is None:
        reasons.append("no_opposite_ask")
    elif float(opposite_ask) > profile.max_opposite_ask:
        reasons.append("opposite_ask_too_high")

    if float(opposite.get("ask_depth_usd") or 0) < profile.min_depth_usd:
        reasons.append("opposite_depth_low")

    spread = book.get("spread")
    if spread is not None and float(spread) > profile.max_spread:
        reasons.append("spread_wide")

    if profile.require_hedgeable:
        opp_ok, _, opp_reasons = _fill_ok(opposite, profile, profile.max_opposite_ask)
        if not opp_ok:
            reasons.append("opposite_not_hedgeable")
            for r in opp_reasons:
                reasons.append(f"opposite_{r}")

    seen: set[str] = set()
    unique: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def hedge_reasons(
    snapshot: dict[str, Any],
    opposite_side: str,
    profile: DualLegProfile,
) -> list[str]:
    reasons: list[str] = []
    t_remaining = float(snapshot.get("t_remaining") or 0)
    if t_remaining < profile.second_t_min:
        reasons.append("too_late_second_leg")
    book = _book(snapshot, opposite_side)
    # Second leg in paper V2 uses MAX_ASK (same as first), not opposite ask.
    ok, _, fill_reasons = _fill_ok(book, profile, profile.max_ask)
    for r in fill_reasons:
        reasons.append(f"second_{r}")
    return reasons


def evaluate_checkpoint(
    *,
    asset: str,
    snapshot: dict[str, Any],
    profile: DualLegProfile,
    unit_override: float | None = None,
    token_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate first-leg + would-hedge under a profile; build dry FOK intents."""
    assert_btc_only(asset)
    regime = snapshot.get("binance_regime") or snapshot.get("entry_regime") or {}
    if not isinstance(regime, dict):
        regime = {}
    if not regime:
        try:
            from price_feeds import recent_market_regime

            regime = recent_market_regime("btc")
        except Exception as exc:
            regime = {"source": "unavailable", "regime": "unavailable", "error": str(exc)}

    unit = float(unit_override if unit_override is not None else profile.unit_usd)
    # Temporarily scale depth requirement with unit for $30 stress without mutating profile.
    scaled = DualLegProfile(
        name=profile.name,
        unit_usd=unit,
        max_ask=profile.max_ask,
        min_depth_usd=profile.min_depth_usd,
        depth_mult=profile.depth_mult,
        ask_slip=profile.ask_slip,
        strict_fill=profile.strict_fill,
        require_hedgeable=profile.require_hedgeable,
        first_t_min=profile.first_t_min,
        second_t_min=profile.second_t_min,
        max_abs_delta_pct=profile.max_abs_delta_pct,
        max_opposite_ask=profile.max_opposite_ask,
        max_spread=profile.max_spread,
        allowed_regimes=profile.allowed_regimes,
        require_binance_regime=profile.require_binance_regime,
        bonus_watch_ask=profile.bonus_watch_ask,
    )

    side_reasons = {
        side: first_leg_reasons(snapshot, side, regime, scaled)
        for side in ("up", "down")
    }
    asks = {
        side: _book(snapshot, side).get("best_ask") for side in ("up", "down")
    }
    eligible = [
        side
        for side in ("up", "down")
        if asks[side] is not None and not side_reasons[side]
    ]
    eligible.sort(key=lambda s: float(asks[s]))

    would_leg1 = None
    would_leg2 = None
    intents: list[dict[str, Any]] = []
    skip_reasons: list[str] = []

    if not eligible:
        # Aggregate skip histogram keys
        for side in ("up", "down"):
            for r in side_reasons[side]:
                skip_reasons.append(r)
    else:
        first_side = eligible[0]
        book1 = _book(snapshot, first_side)
        sim1 = simulate_fok_fill(book1, profile=scaled, max_ask=scaled.max_ask, unit_usd=unit)
        if not sim1.get("filled"):
            skip_reasons.append(f"leg1_nofill_{sim1.get('no_fill_reason')}")
        else:
            tok = (token_ids or {}).get(first_side, f"DRY_TOKEN_{first_side.upper()}")
            intent1 = build_fok_buy_intent(
                token_id=tok,
                side=first_side,
                decision_ask=float(sim1["decision_ask"]),
                stake_usd=unit,
                max_fill_price=scaled.max_ask,
                profile=scaled.name,
                slug=str(snapshot.get("slug") or ""),
            )
            intents.append(intent1.to_dict())
            would_leg1 = {
                "side": first_side,
                **sim1,
                "fok_intent": intent1.to_dict(),
            }
            opposite = "down" if first_side == "up" else "up"
            h_reasons = hedge_reasons(snapshot, opposite, scaled)
            book2 = _book(snapshot, opposite)
            # Same-checkpoint hedge (strict): second leg must clear MAX_ASK like paper.
            sim2_strict = simulate_fok_fill(
                book2, profile=scaled, max_ask=scaled.max_ask, unit_usd=unit
            )
            # Path-to-hedge signal: opposite fillable under MAX_OPPOSITE_ASK (paper waits).
            sim2_path = simulate_fok_fill(
                book2, profile=scaled, max_ask=scaled.max_opposite_ask, unit_usd=unit
            )
            same_cp_hedge = bool(sim2_strict.get("filled")) and not h_reasons
            path_hedgeable = bool(sim2_path.get("filled"))
            if same_cp_hedge:
                tok2 = (token_ids or {}).get(opposite, f"DRY_TOKEN_{opposite.upper()}")
                intent2 = build_fok_buy_intent(
                    token_id=tok2,
                    side=opposite,
                    decision_ask=float(sim2_strict["decision_ask"]),
                    stake_usd=unit,
                    max_fill_price=scaled.max_ask,
                    profile=scaled.name,
                    slug=str(snapshot.get("slug") or ""),
                )
                intents.append(intent2.to_dict())
                fee1 = float(would_leg1.get("estimated_taker_fee") or 0)
                fee2 = float(sim2_strict.get("estimated_taker_fee") or 0)
                shares1 = float(would_leg1.get("shares") or 0)
                shares2 = float(sim2_strict.get("shares") or 0)
                total_stake = unit * 2
                guaranteed = min(shares1, shares2) - total_stake - fee1 - fee2
                would_leg2 = {
                    "side": opposite,
                    "would_hedge": True,
                    "same_checkpoint_hedge": True,
                    "path_hedgeable_opposite": True,
                    "stranded_risk": False,
                    **sim2_strict,
                    "fok_intent": intent2.to_dict(),
                    "estimated_total_fee": round(fee1 + fee2, 4),
                    "guaranteed_floor_est": round(guaranteed, 4),
                }
            else:
                would_leg2 = {
                    "side": opposite,
                    "would_hedge": False,
                    "same_checkpoint_hedge": False,
                    "path_hedgeable_opposite": path_hedgeable,
                    "stranded_risk": not path_hedgeable,
                    "hedge_skip_reasons": h_reasons
                    or [sim2_strict.get("no_fill_reason")],
                    "path_sim": {
                        k: sim2_path.get(k)
                        for k in (
                            "filled",
                            "decision_ask",
                            "fill_ask",
                            "ask_depth_usd",
                            "no_fill_reason",
                        )
                    },
                }
                skip_reasons.extend(h_reasons)

    depth_stress = {}

    for u in (3.0, 30.0):
        stress_prof = DualLegProfile(
            name=scaled.name,
            unit_usd=u,
            max_ask=scaled.max_ask,
            min_depth_usd=scaled.min_depth_usd,
            depth_mult=scaled.depth_mult,
            ask_slip=scaled.ask_slip,
            strict_fill=scaled.strict_fill,
            require_hedgeable=scaled.require_hedgeable,
            first_t_min=scaled.first_t_min,
            second_t_min=scaled.second_t_min,
            max_abs_delta_pct=scaled.max_abs_delta_pct,
            max_opposite_ask=scaled.max_opposite_ask,
            max_spread=scaled.max_spread,
            allowed_regimes=scaled.allowed_regimes,
        )
        need = stress_prof.required_depth_usd()
        depth_stress[f"unit_{int(u)}"] = {
            "required_depth_usd": need,
            "up_depth_ok": float(_book(snapshot, "up").get("ask_depth_usd") or 0) >= need,
            "down_depth_ok": float(_book(snapshot, "down").get("ask_depth_usd") or 0)
            >= need,
            "up_depth_usd": float(_book(snapshot, "up").get("ask_depth_usd") or 0),
            "down_depth_usd": float(_book(snapshot, "down").get("ask_depth_usd") or 0),
        }

    return {
        "timestamp_hkt": now_hkt(),
        "strategy": STRATEGY_LIVE,
        "profile": profile.name,
        "asset": "BTC",
        "dry_run": True,
        "posted_live_order": False,
        "slug": snapshot.get("slug"),
        "checkpoint": snapshot.get("checkpoint"),
        "t_remaining": snapshot.get("t_remaining"),
        "delta_pct": snapshot.get("delta_pct"),
        "regime": regime.get("regime"),
        "unit_usd": unit,
        "required_depth_usd": scaled.required_depth_usd(),
        "up_gate_reasons": side_reasons["up"],
        "down_gate_reasons": side_reasons["down"],
        "eligible_sides": eligible,
        "would_leg1": would_leg1,
        "would_leg2": would_leg2,
        "would_enter_leg1": would_leg1 is not None,
        "would_hedge": bool(would_leg2 and would_leg2.get("would_hedge")),
        "same_checkpoint_hedge": bool(would_leg2 and would_leg2.get("same_checkpoint_hedge")),
        "path_hedgeable_opposite": bool(would_leg2 and would_leg2.get("path_hedgeable_opposite")),
        "stranded_risk": bool(
            would_leg1 and (not would_leg2 or would_leg2.get("stranded_risk"))
        ),
        "fok_intents_would_send": intents,
        "skip_reasons": skip_reasons,
        "depth_stress": depth_stress,
    }


def append_dry_log(row: dict[str, Any], path: Path | None = None) -> None:
    target = path or DRY_RUN_LOG
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a") as handle:
        handle.write(json.dumps(row, default=str) + "\n")


LIVE_EXEC_LOG = LOG_DIR / "dual_leg_live_exec.jsonl"


def execute_fok_intent(
    intent: FokOrderIntent,
    *,
    force_live: bool = False,
) -> dict[str, Any]:
    """Execute or dry-log a FOK intent.

    - DUAL_LEG_LIVE_DRY_RUN=1 (default): never posts; logs would_post only.
    - DUAL_LEG_LIVE_DRY_RUN=0 + force_live=True: posts via btc_clob.place_fok_buy_chase.
    Shadow / dry runners must keep DRY_RUN=1 and never pass force_live=True.
    """
    if dry_run_enabled():
        if force_live:
            raise RuntimeError(
                "force_live=True refused while DUAL_LEG_LIVE_DRY_RUN=1"
            )
        result = {
            "status": "dry_run",
            "posted": False,
            "would_post": intent.to_dict(),
            "timestamp_hkt": now_hkt(),
            "note": "Order built and logged only; create_and_post_market_order NOT called",
        }
        append_dry_log({"event": "execute_fok_intent", **result})
        return result

    if not force_live:
        raise RuntimeError(
            "DUAL_LEG_LIVE_DRY_RUN=0 but force_live=False — refusing silent post"
        )

    from btc_clob import place_fok_buy_chase

    try:
        order = place_fok_buy_chase(
            token_id=str(intent.token_id),
            decision_ask=float(intent.decision_ask),
            stake_usd=float(intent.stake_usd),
            max_fill_price=float(intent.max_fill_price),
            tick_size=str(intent.tick_size or "0.01"),
            neg_risk=bool(intent.neg_risk),
            min_size=float(intent.min_size or 5.0),
        )
        result = {
            "status": "live_filled",
            "posted": True,
            "force_live": True,
            "dry_run": False,
            "intent": intent.to_dict(),
            "order": order,
            "timestamp_hkt": now_hkt(),
            "note": "FOK posted via btc_clob.place_fok_buy_chase",
        }
    except Exception as exc:
        result = {
            "status": "live_error",
            "posted": False,
            "force_live": True,
            "dry_run": False,
            "intent": intent.to_dict(),
            "error": str(exc),
            "timestamp_hkt": now_hkt(),
            "note": "FOK live attempt failed (no confirmed fill)",
        }
    append_dry_log({"event": "execute_fok_intent_live", **result}, LIVE_EXEC_LOG)
    append_dry_log({"event": "execute_fok_intent_live", **result})
    return result



def live_unit_usd(default: float = 6.0) -> float:
    """Stake per leg for live path. Prefers DUAL_LEG_LIVE_UNIT_USD (paper keeps DUAL_LEG_UNIT_USD)."""
    for key in ("DUAL_LEG_LIVE_UNIT_USD", "DUAL_LEG_UNIT_USD"):
        raw = os.getenv(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return float(default)


def _env_file_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    prefix = f"{key}="
    found: str | None = None
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or not raw.startswith(prefix):
            continue
        found = raw.split("=", 1)[1].strip().strip('"').strip("'")
    return found


def read_live_unit_usd(default: float = 6.0) -> float:
    """Per-leg stake for the next live entry.

    Re-reads ``DUAL_LEG_LIVE_ENV_FILE`` so a desk bet-size save applies on the
    next window. The process environment is only the fallback from startup.
    """
    explicit = (os.getenv("DUAL_LEG_LIVE_ENV_FILE") or "").strip()
    if explicit:
        raw = _env_file_value(Path(explicit), "DUAL_LEG_LIVE_UNIT_USD")
        if raw:
            try:
                value = float(raw)
            except ValueError:
                value = 0.0
            if value > 0:
                return value
    return live_unit_usd(default)


def live_profile_name(default: str = "v1") -> str:
    name = (os.getenv("DUAL_LEG_LIVE_PROFILE", default) or default).strip().lower()
    return name if name in PROFILES else default


def profile_to_dict(profile: DualLegProfile) -> dict[str, Any]:
    d = asdict(profile)
    d["required_depth_usd"] = profile.required_depth_usd()
    d["allowed_regimes"] = list(profile.allowed_regimes)
    return d
