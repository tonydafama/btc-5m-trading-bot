"""Shared trade-row analytics for backtest, fee reconciliation, and strategy research."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

HKT = ZoneInfo("Asia/Hong_Kong")
SCHEMA_VERSION = 1
TAKER_FEE_RATE = 0.07


def now_hkt_str() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def est_taker_fee_usd(*, stake: float, ask: float, shares: float | None = None) -> float:
    if ask <= 0 or stake <= 0:
        return 0.0
    qty = shares if shares and shares > 0 else stake / ask
    try:
        from polymarket_fees import estimate_taker_fee

        return float(estimate_taker_fee(qty, ask))
    except Exception:
        return round(qty * TAKER_FEE_RATE * ask * (1 - ask), 4)


def cell_key_from_row(row: dict[str, Any]) -> str | None:
    asset = row.get("asset")
    period = row.get("period")
    tier = row.get("tier")
    hour = row.get("hour_et")
    if asset is None or period is None or tier is None or hour is None:
        return None
    return f"{str(asset).upper()}|{period}|{tier}|{int(hour):02d}"


def compact_snapshot(snap: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snap:
        return None
    out: dict[str, Any] = {}
    for key in (
        "checkpoint",
        "t_remaining",
        "ask",
        "side",
        "delta_usd",
        "delta_pct",
        "btc_price",
        "eth_price",
        "open_btc_price",
        "open_eth_price",
        "skip_reasons",
    ):
        if snap.get(key) is not None:
            out[key] = snap.get(key)
    for side in ("up_book", "down_book"):
        book = snap.get(side)
        if not isinstance(book, dict):
            continue
        slim = {k: book.get(k) for k in ("best_ask", "best_bid", "spread", "depth") if book.get(k) is not None}
        if slim:
            out[side] = slim
    return out or None


def checkpoint_trail(checkpoints: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not checkpoints:
        return []
    trail: list[dict[str, Any]] = []
    for snap in checkpoints:
        if not isinstance(snap, dict):
            continue
        item = {
            "checkpoint": snap.get("checkpoint"),
            "t_remaining": snap.get("t_remaining"),
            "ask": round(float(snap["ask"]), 4) if snap.get("ask") is not None else None,
            "side": snap.get("side"),
        }
        trail.append({k: v for k, v in item.items() if v is not None})
    return trail


def _row_shares(row: dict[str, Any]) -> float | None:
    shares = row.get("shares")
    if shares is not None and float(shares) > 0:
        return float(shares)
    order = row.get("live_order") or {}
    for key in ("takingAmount", "taking_amount", "size"):
        val = order.get(key)
        if val is not None and float(val) > 0:
            return float(val)
    stake = float(row.get("stake") or 0)
    ask = float(row.get("fill_price") or row.get("entry_ask") or 0)
    if stake > 0 and ask > 0:
        return round(stake / ask, 4)
    return None


def enrich_fill_metrics(row: dict[str, Any], *, order: dict[str, Any] | None = None) -> None:
    if order:
        row.setdefault("live_order", order)
    shares = _row_shares(row)
    if shares is not None:
        row["shares"] = round(shares, 4)
    decision = row.get("decision_ask")
    fill = row.get("fill_price") or row.get("entry_ask")
    if decision is not None and fill is not None:
        decision_f = float(decision)
        fill_f = float(fill)
        slip = round(fill_f - decision_f, 4)
        row["fill_slippage"] = slip
        if decision_f > 0:
            row["fill_slippage_bps"] = round(slip / decision_f * 10_000, 1)
    stake = float(row.get("stake") or 0)
    ask = float(row.get("entry_ask") or row.get("fill_price") or 0)
    if stake > 0 and ask > 0:
        row["est_fee_usd"] = round(est_taker_fee_usd(stake=stake, ask=ask, shares=shares), 4)


def enrich_settlement_pnl(row: dict[str, Any]) -> None:
    from new_strategy import calc_loss_pnl, calc_win_pnl

    result = str(row.get("trade_result") or "").upper()
    if result not in ("WIN", "LOSS"):
        return
    stake = float(row.get("stake") or 0)
    ask = float(row.get("entry_ask") or row.get("fill_price") or 0)
    shares = _row_shares(row)
    if row.get("gross_pnl") is None:
        legacy = float(row.get("pnl") or 0)
        gross = calc_win_pnl(stake, ask) if result == "WIN" else calc_loss_pnl(stake)
        row["gross_pnl"] = round(gross, 2) if stake > 0 else round(legacy, 2)
    fee = row.get("est_fee_usd")
    if fee is None:
        fee = est_taker_fee_usd(stake=stake, ask=ask, shares=shares)
        row["est_fee_usd"] = round(fee, 4)
    else:
        fee = float(fee)
    gross = float(row["gross_pnl"])
    net = round(gross - fee, 2)
    row["net_pnl"] = net
    row["pnl"] = net
    row.setdefault("settled_at_hkt", now_hkt_str())
    row.setdefault("settled_at_utc", datetime.now(timezone.utc).isoformat())


def enrich_v1_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out.setdefault("data_schema_version", SCHEMA_VERSION)
    enrich_fill_metrics(out)
    if str(out.get("trade_result") or "").upper() in ("WIN", "LOSS") and out.get("entered"):
        enrich_settlement_pnl(out)
    return out


def enrich_alpha_row(
    row: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None = None,
    checkpoints: list[dict[str, Any]] | None = None,
    wallet_cash: float | None = None,
    order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = dict(row)
    out.setdefault("data_schema_version", SCHEMA_VERSION)
    key = cell_key_from_row(out)
    if key:
        out["cell_key"] = key
    if wallet_cash is not None:
        out["wallet_cash_at_entry"] = round(float(wallet_cash), 2)
    snap = compact_snapshot(snapshot)
    if snap:
        out["entry_snapshot"] = snap
    trail = checkpoint_trail(checkpoints)
    if trail:
        out["checkpoint_trail"] = trail
    if order:
        enrich_fill_metrics(out, order=order)
    elif out.get("filled") or out.get("live_order"):
        enrich_fill_metrics(out)
    if str(out.get("trade_result") or "").upper() in ("WIN", "LOSS") and out.get("filled"):
        enrich_settlement_pnl(out)
    return out


def backfill_alpha_trade_row(row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    before = json_dumps_stable(row)
    updated = enrich_alpha_row(row)
    changed = json_dumps_stable(updated) != before
    return updated, changed


def json_dumps_stable(row: dict[str, Any]) -> str:
    import json

    return json.dumps(row, sort_keys=True, default=str)
