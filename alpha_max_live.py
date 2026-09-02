"""Alpha-Max live trading — hour-cell entries on a dedicated bot instance."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from bot_instance import log_root
from bot_common import setup_logger
from new_strategy import (
    bought_label,
    calc_loss_pnl,
    calc_win_pnl,
    now_hkt_str,
    resolved_label,
    side_to_contract,
    trade_result,
)
from trade_analytics import (
    SCHEMA_VERSION,
    enrich_alpha_row,
    enrich_settlement_pnl,
    backfill_alpha_trade_row,
)

HKT = ZoneInfo("Asia/Hong_Kong")

LIVE_LOG_FILE = log_root() / "alpha_max_trades_live.json"
LIVE_SESSION_FILE = log_root() / "alpha_max_live_session.json"
LIVE_STATE_FILE = log_root() / "alpha_max_live_state.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def daily_loss_limit_usd() -> float:
    configured = os.getenv("ALPHA_MAX_DAILY_LOSS_LIMIT")
    return min(float(configured), 125.0) if configured else 125.0


def trading_mode() -> str:
    return os.getenv("TRADING_MODE", "paper").lower()


def load_live_session() -> dict[str, Any]:
    return _load_json(LIVE_SESSION_FILE, {})


def load_live_log() -> list[dict[str, Any]]:
    rows = _load_json(LIVE_LOG_FILE, [])
    return rows if isinstance(rows, list) else []


def save_live_log(rows: list[dict[str, Any]]) -> None:
    _save_json(LIVE_LOG_FILE, rows[-2000:])


def load_live_state() -> dict[str, Any]:
    return _load_json(LIVE_STATE_FILE, {})


def save_live_state(state: dict[str, Any]) -> None:
    _save_json(LIVE_STATE_FILE, state)


def backfill_alpha_live_analytics(*, force: bool = False) -> int:
    """Backfill fee / cell / snapshot analytics on historical Alpha live rows."""
    state = load_live_state()
    if not force and state.get("analytics_schema_version") == SCHEMA_VERSION:
        return 0
    rows = load_live_log()
    updated: list[dict[str, Any]] = []
    changed = 0
    for row in rows:
        cps = _checkpoints_for_slug(str(row.get("asset") or "BTC"), str(row.get("slug") or ""), [])
        new_row, did = backfill_alpha_trade_row(row)
        if cps and not new_row.get("checkpoint_trail"):
            new_row = enrich_alpha_row(new_row, checkpoints=cps)
            did = True
        updated.append(new_row)
        if did:
            changed += 1
    if changed or force:
        save_live_log(updated)
        state["analytics_schema_version"] = SCHEMA_VERSION
        state["analytics_backfill_at_hkt"] = now_hkt_str()
        save_live_state(state)
    return changed


def slug_logged(slug: str) -> bool:
    return any(r.get("slug") == slug for r in load_live_log())


def _wallet_cash() -> float:
    try:
        from polymarket_wallet import fetch_wallet_status

        return float(fetch_wallet_status().get("cash_usdc") or 0)
    except Exception:
        return 0.0


def _row_net_pnl(row: dict) -> float:
    if row.get("gross_pnl") is not None:
        fee = float(row.get("est_fee_usd") or 0)
        return round(float(row.get("pnl") or float(row["gross_pnl"]) - fee), 2)
    gross = float(row.get("pnl") or 0)
    ask = float(row.get("entry_ask") or 0.5)
    stake = float(row.get("stake") or 0)
    if ask <= 0 or stake <= 0:
        return round(gross, 2)
    try:
        from polymarket_fees import estimate_taker_fee

        fee = estimate_taker_fee(stake / ask, ask)
    except Exception:
        fee = 0.0
    return round(gross - fee, 2)


def daily_realized_pnl_hkt() -> float:
    today = now_hkt_str()[:10]
    total = 0.0
    for row in load_live_log():
        if row.get("trade_result") not in ("WIN", "LOSS"):
            continue
        if not str(row.get("timestamp_hkt", "")).startswith(today):
            continue
        total += _row_net_pnl(row)
    return round(total, 2)


def daily_loss_limit_hit() -> bool:
    try:
        from bot_control import daily_stop_released

        if daily_stop_released("alpha_max"):
            return False
    except Exception:
        pass
    return daily_realized_pnl_hkt() <= -daily_loss_limit_usd()


def ensure_live_session(*, starting_wallet_usdc: float | None = None) -> dict[str, Any]:
    sess = load_live_session()
    if not sess.get("session_id"):
        now = datetime.now(timezone.utc)
        wallet = starting_wallet_usdc if starting_wallet_usdc is not None else _wallet_cash()
        sess = {
            "session_id": "alpha-max-live-" + now.strftime("%Y%m%d-%H%M%S"),
            "strategy": "Alpha-Max Hour Cells",
            "mode": "live",
            "started_at_utc": now.isoformat(),
            "started_at_hkt": now.astimezone(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
            "starting_wallet_usdc": round(float(wallet), 2),
            "bot_instance": os.getenv("BOT_INSTANCE", "bot2"),
        }
        _save_json(LIVE_SESSION_FILE, sess)
        save_live_state({
            "trades_entered": 0,
            "trades_skipped": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl": 0.0,
        })
    backfill_alpha_live_analytics()
    return sess


def _snapshot_to_alpha_candidate(asset: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    from alpha_max import _candidate

    return _candidate(asset.upper(), snapshot)


def _build_row(
    *,
    asset: str,
    slug: str,
    title: str,
    cand: dict[str, Any],
    stake: float,
) -> dict[str, Any]:
    side = cand.get("side", "up")
    ask = float(cand["ask"])
    contract = side_to_contract(side)
    return {
        "timestamp_hkt": now_hkt_str(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": load_live_session().get("session_id"),
        "strategy": "ALPHA-MAX",
        "mode": "live",
        "asset": asset.upper(),
        "slug": slug,
        "market": title,
        "tier": cand["tier"],
        "period": cand["period"],
        "hour_et": cand["hour_et"],
        "checkpoint": cand.get("checkpoint"),
        "t_remaining": cand.get("t_remaining"),
        "side": side,
        "bought_contract": contract,
        "bought_label": bought_label(contract, ask, True),
        "entry_ask": round(ask, 4),
        "decision_ask": round(ask, 4),
        "stake_intended": round(stake, 2),
        "stake": stake,
        "multiplier": "alpha-max",
        "entered": True,
        "filled": False,
        "resolved_outcome": "PENDING",
        "resolved_label": "PENDING",
        "trade_result": "PENDING",
        "pnl": 0.0,
        "status": "open",
        "strategy_reason": (
            f"Alpha-Max {asset.upper()} {cand['period']} {cand['tier']} "
            f"{cand['hour_et']:02d}:00 ET cell"
        ),
    }


def _place_live_buy(
    *,
    asset: str,
    slug: str,
    row: dict[str, Any],
    stake: float,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    from btc_clob import compute_max_fill_price, place_live_buy_confirmed
    from paper_engine import _apply_live_fill, _amount_float
    from polymarket_btc import fetch_event_by_slug, parse_market

    side = row["side"]
    ask = float(row["entry_ask"])
    event = fetch_event_by_slug(slug)
    if not event:
        raise RuntimeError(f"No event for slug {slug}")
    market = parse_market(event)
    if not market:
        raise RuntimeError("Could not parse market")
    token_id = market.up_token_id if side.lower() == "up" else market.down_token_id
    mk = (event.get("markets") or [{}])[0]
    tick = str(mk.get("tickSize") or "0.01")
    neg_risk = bool(mk.get("negRisk", False))
    min_size = float(mk.get("orderMinSize") or 5)
    max_fill = compute_max_fill_price(ask, 0.90)
    row["max_fill_price"] = max_fill
    t_rem = float(snapshot.get("t_remaining") or 0)
    deadline = time.time() + max(12.0, min(35.0, t_rem - 15.0)) if t_rem > 15 else time.time() + 10.0

    order = place_live_buy_confirmed(
        token_id=token_id,
        decision_ask=ask,
        stake_usd=stake,
        max_fill_price=max_fill,
        tick_size=tick,
        neg_risk=neg_risk,
        min_size=min_size,
        deadline_ts=deadline,
    )
    row["execution_path"] = order.get("execution_path")
    row["order_id"] = order.get("orderID") or order.get("order_id")
    bankroll = _wallet_cash()
    filled = _apply_live_fill(row, order, stake, bankroll)
    if filled:
        shares = _amount_float(order.get("takingAmount") or order.get("taking_amount"))
        spent = float(row["stake"])
        if shares > 0:
            row["fill_price"] = round(spent / shares, 4)
            row["entry_ask"] = row["fill_price"]
            row["bought_label"] = bought_label(row["bought_contract"], row["fill_price"], True)
    else:
        row["trade_result"] = "NO_FILL"
        row["status"] = "unfilled"
        row["entered"] = False
        row["stake"] = 0.0
        row["strategy_reason"] = row["strategy_reason"] + f" · NO_FILL ({order.get('status')})"
    return row


def _checkpoints_for_slug(
    asset: str,
    slug: str,
    checkpoints: list[dict[str, Any]],
    *,
    log_dir: Path | None = None,
) -> list[dict[str, Any]]:
    if checkpoints:
        return checkpoints
    if not slug:
        return []
    root = log_dir or log_root()
    asset_l = str(asset).lower()
    wf = root / f"alpha_max_{asset_l}_windows.json"
    if wf.exists():
        try:
            windows = json.loads(wf.read_text())
            for rec in reversed(windows if isinstance(windows, list) else []):
                if rec.get("slug") == slug:
                    cps = list(rec.get("checkpoints") or [])
                    if cps:
                        return cps
        except Exception:
            pass
    obs = root / f"alpha_max_{asset_l}_observations.jsonl"
    if obs.exists():
        try:
            rows: list[dict[str, Any]] = []
            for line in obs.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("slug") == slug:
                    rows.append(row)
            if rows:
                return rows
        except Exception:
            pass
    return []


def _snapshot_meta(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not snapshot or snapshot.get("ask") is None:
        return {}
    from alpha_max import _dt_from_snapshot, period_for_dt, tier_for_ask

    ask = float(snapshot["ask"])
    tier = tier_for_ask(ask)
    dt_et = _dt_from_snapshot(snapshot)
    return {
        "tier": tier,
        "period": period_for_dt(dt_et),
        "hour_et": dt_et.hour,
    }


def enrich_alpha_skip_display(row: dict[str, Any], *, log_dir: Path | None = None) -> dict[str, Any]:
    """Backfill skip reason and ask for rows finalized without in-memory checkpoints."""
    if str(row.get("trade_result") or "").upper() != "SKIPPED":
        return row
    reason = str(row.get("strategy_reason") or "")
    slug = str(row.get("slug") or "")
    asset = str(row.get("asset") or "BTC")
    needs_reason = not reason or "no checkpoints" in reason.lower()
    needs_ask = row.get("entry_ask") is None
    if not needs_reason and not needs_ask:
        return row
    cps = _checkpoints_for_slug(asset, slug, [], log_dir=log_dir)
    if not cps:
        return row
    out = dict(row)
    snap = cps[-1]
    if needs_reason:
        out["strategy_reason"] = _best_skip_reason(asset, cps)
    if needs_ask and snap.get("ask") is not None:
        out["entry_ask"] = round(float(snap["ask"]), 4)
    if not out.get("side") and snap.get("side"):
        out["side"] = snap["side"]
    if not out.get("bought_label") and snap.get("side") and snap.get("ask") is not None:
        out["bought_label"] = f"{str(snap['side']).upper()} @ {float(snap['ask']):.2f}"
    meta = _snapshot_meta(snap)
    for key, val in meta.items():
        if out.get(key) is None:
            out[key] = val
    return out


def _best_skip_reason(asset: str, checkpoints: list[dict[str, Any]]) -> str:
    from alpha_max import alpha_skip_reason, pick_alpha_candidate

    if pick_alpha_candidate(asset.upper(), checkpoints):
        return "Skipped: cell matched but no live entry"
    for snap in reversed(checkpoints):
        if snap.get("ask") is not None:
            return alpha_skip_reason(asset, snap)
    for snap in reversed(checkpoints):
        blockers = [str(r) for r in (snap.get("skip_reasons") or []) if r]
        if blockers:
            detail = ", ".join(blockers)
            if any("no_ask" in b for b in blockers):
                return "Skipped: no ask"
            return f"Skipped: timing/filter — {detail}"
    return "Skipped: no window data (observer offline or late start)"


def _append_skip_row(
    *,
    asset: str,
    slug: str,
    title: str,
    reason: str,
    snapshot: dict[str, Any] | None = None,
    checkpoints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    side = (snapshot or {}).get("side")
    ask = (snapshot or {}).get("ask")
    meta = _snapshot_meta(snapshot)
    contract = side_to_contract(side) if side else None
    row = {
        "timestamp_hkt": now_hkt_str(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": load_live_session().get("session_id"),
        "strategy": "ALPHA-MAX",
        "mode": "live",
        "asset": asset.upper(),
        "slug": slug,
        "market": title,
        "tier": meta.get("tier"),
        "period": meta.get("period"),
        "hour_et": meta.get("hour_et"),
        "checkpoint": (snapshot or {}).get("checkpoint"),
        "t_remaining": (snapshot or {}).get("t_remaining"),
        "side": side,
        "bought_contract": contract or "NONE",
        "bought_label": bought_label(contract, float(ask), False) if contract and ask is not None else None,
        "entry_ask": round(float(ask), 4) if ask is not None else None,
        "stake": 0.0,
        "entered": False,
        "filled": False,
        "trade_result": "SKIPPED",
        "pnl": 0.0,
        "status": "skipped",
        "strategy_reason": reason,
        "skip_category": "alpha_cell",
    }
    row = enrich_alpha_row(row, snapshot=snapshot, checkpoints=checkpoints)
    rows = load_live_log()
    rows.append(row)
    save_live_log(rows)
    state = load_live_state()
    state["trades_skipped"] = int(state.get("trades_skipped", 0)) + 1
    save_live_state(state)
    return row


def finalize_alpha_live_window(
    *,
    asset: str,
    slug: str,
    title: str,
    checkpoints: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Write one SKIPPED row per window when no live entry was logged."""
    if trading_mode() != "live" or not slug or slug_logged(slug):
        return None
    try:
        from bot_control import is_bot_paused

        if is_bot_paused("alpha_max"):
            snap = checkpoints[-1] if checkpoints else None
            return _append_skip_row(
                asset=asset, slug=slug, title=title,
                reason="Skipped: Alpha-Max paused",
                snapshot=snap,
                checkpoints=checkpoints,
            )
    except Exception:
        pass
    if daily_loss_limit_hit():
        snap = checkpoints[-1] if checkpoints else None
        return _append_skip_row(
            asset=asset, slug=slug, title=title,
            reason=f"Skipped: daily loss limit −${daily_loss_limit_usd():.0f} hit",
            snapshot=snap,
            checkpoints=checkpoints,
        )
    snap = checkpoints[-1] if checkpoints else {}
    cps = _checkpoints_for_slug(asset, slug, list(checkpoints))
    reason = _best_skip_reason(asset, cps)
    snap = cps[-1] if cps else snap
    return _append_skip_row(asset=asset, slug=slug, title=title, reason=reason, snapshot=snap or None, checkpoints=cps)


def try_alpha_live_entry_if_ready(
    *,
    asset: str,
    slug: str,
    title: str,
    snapshot: dict[str, Any],
    checkpoints: list[dict[str, Any]] | None = None,
) -> Optional[dict[str, Any]]:
    """Enter live Alpha-Max trade when checkpoint matches a positive hour cell."""
    if trading_mode() != "live":
        return None
    try:
        from bot_control import is_bot_paused

        if is_bot_paused("alpha_max"):
            return None
    except Exception:
        pass
    if not load_live_session().get("session_id"):
        return None
    if slug_logged(slug):
        return None
    if daily_loss_limit_hit():
        return None

    cand = _snapshot_to_alpha_candidate(asset, snapshot)
    if not cand:
        return None

    from alpha_max import stake_for

    stake = stake_for(asset.upper(), cand["tier"], cand["period"])
    wallet = _wallet_cash()
    if stake > wallet:
        setup_logger().warning(
            "Alpha-Max %s skip %s: wallet $%.2f < stake $%.2f",
            asset, slug, wallet, stake,
        )
        return _append_skip_row(
            asset=asset,
            slug=slug,
            title=title,
            reason=f"Skipped: wallet ${wallet:.2f} < stake ${stake:.2f}",
            snapshot={**snapshot, **cand},
        )

    row = _build_row(asset=asset, slug=slug, title=title, cand=cand, stake=stake)
    wallet_before = wallet
    state = load_live_state()
    try:
        row = _place_live_buy(asset=asset, slug=slug, row=row, stake=stake, snapshot=snapshot)
    except Exception as exc:
        row["trade_result"] = "FAILED"
        row["status"] = "failed"
        row["entered"] = False
        row["stake"] = 0.0
        row["strategy_reason"] = row["strategy_reason"] + f" · FAILED: {exc}"
        setup_logger().error("Alpha-Max live order failed %s: %s", slug, exc)

    row = enrich_alpha_row(
        row,
        snapshot={**snapshot, **cand},
        checkpoints=checkpoints,
        wallet_cash=wallet_before,
        order=row.get("live_order"),
    )

    if row.get("filled"):
        state["trades_entered"] = int(state.get("trades_entered", 0)) + 1
    elif row.get("trade_result") not in ("SKIPPED",):
        state["trades_skipped"] = int(state.get("trades_skipped", 0)) + 1
    save_live_state(state)

    rows = load_live_log()
    rows.append(row)
    save_live_log(rows)
    return row


def settle_stale_alpha_open_trades() -> int:
    """Backfill Polymarket outcomes for Alpha live rows still PENDING (mirrors V1)."""
    count = 0
    now = time.time()
    seen: set[str] = set()
    for row in load_live_log():
        if row.get("trade_result") not in ("PENDING", None):
            continue
        if not row.get("filled"):
            continue
        slug = str(row.get("slug") or "")
        if not slug or slug in seen:
            continue
        try:
            start = int(slug.rsplit("-", 1)[-1])
        except ValueError:
            continue
        if now < start + 330:
            continue
        if settle_alpha_from_polymarket(slug):
            count += 1
            seen.add(slug)
    return count


def settle_alpha_from_polymarket(slug: str) -> Optional[dict[str, Any]]:
    from polymarket_btc import fetch_market_outcome

    resolution = fetch_market_outcome(slug)
    if not resolution:
        return None
    outcome = resolution["outcome"]
    if outcome not in ("up", "down"):
        return None
    resolved = "UP" if outcome == "up" else "DOWN"

    rows = load_live_log()
    settled_row: dict[str, Any] | None = None
    state = load_live_state()
    changed = False

    for row in rows:
        if row.get("slug") != slug:
            continue
        if row.get("trade_result") not in ("PENDING", "NO_FILL", None):
            continue
        if not row.get("filled"):
            continue
        result = trade_result(resolved, row.get("bought_contract", "NONE"), True)
        stake = float(row.get("stake") or 0)
        ask = float(row.get("entry_ask") or 0.5)
        gross = calc_win_pnl(stake, ask) if result == "WIN" else calc_loss_pnl(stake)
        row["resolved_outcome"] = resolved
        row["resolved_label"] = resolved_label(resolved, row.get("bought_contract", "NONE"))
        row["trade_result"] = result
        row["status"] = "closed"
        row["outcome_source"] = "polymarket"
        enrich_settlement_pnl(row)
        enrich_alpha_row(row)
        settled_row = row
        changed = True
        if result == "WIN":
            state["wins"] = int(state.get("wins", 0)) + 1
        else:
            state["losses"] = int(state.get("losses", 0)) + 1
        state["total_pnl"] = round(float(state.get("total_pnl", 0)) + float(row.get("pnl") or 0), 2)
        break

    if changed:
        save_live_log(rows)
        save_live_state(state)
    return settled_row


def _latest_live_row_for_slug(slug: str) -> dict[str, Any] | None:
    for row in reversed(load_live_log()):
        if row.get("slug") == slug:
            return row
    return None


def finalize_alpha_observer_window(record: dict[str, Any]) -> dict[str, Any]:
    """Finalize one Alpha observer window: skip/settle, attach outcome, persist research."""
    slug = str(record.get("slug") or "")
    asset = str(record.get("asset") or "BTC")
    out = dict(record)
    if not out.get("live_trade"):
        skip_row = finalize_alpha_live_window(
            asset=asset,
            slug=slug,
            title=str(out.get("title") or ""),
            checkpoints=list(out.get("checkpoints") or []),
        )
        if skip_row:
            out["skip_row"] = {"trade_result": skip_row.get("trade_result"), "strategy_reason": skip_row.get("strategy_reason")}
    settle_alpha_from_polymarket(slug)
    live_row = _latest_live_row_for_slug(slug)
    if live_row:
        out["live_trade"] = live_row
    try:
        from polymarket_btc import fetch_market_outcome

        resolution = fetch_market_outcome(slug)
        if resolution and resolution.get("outcome") in ("up", "down"):
            out["outcome"] = resolution["outcome"]
            out["outcome_source"] = "polymarket"
    except Exception:
        pass
    out["finalized_at_utc"] = datetime.now(timezone.utc).isoformat()
    try:
        from alpha_max_research import record_window_evaluation

        record_window_evaluation(
            asset=asset,
            slug=slug,
            title=str(out.get("title") or ""),
            checkpoints=list(out.get("checkpoints") or []),
            outcome=out.get("outcome"),
            source="live",
        )
    except Exception as exc:
        setup_logger().warning("Alpha research log failed %s: %s", slug, exc)
    return out


def alpha_max_live_summary() -> dict[str, Any]:
    from alpha_max import alpha_max_summary

    paper = alpha_max_summary()
    sess = load_live_session()
    rows = load_live_log()
    closed = [r for r in rows if r.get("trade_result") in ("WIN", "LOSS")]
    wins = [r for r in closed if r.get("trade_result") == "WIN"]
    pnl = round(sum(float(r.get("pnl") or 0) for r in closed), 2)
    today = now_hkt_str()[:10]
    today_rows = [r for r in rows if str(r.get("timestamp_hkt", "")).startswith(today)]
    wallet = _wallet_cash()
    start = float(sess.get("starting_wallet_usdc") or wallet)
    return {
        **paper,
        "mode": "live" if trading_mode() == "live" and sess.get("session_id") else paper.get("mode"),
        "live_session": sess,
        "live_trades_closed": len(closed),
        "live_wins": len(wins),
        "live_losses": len(closed) - len(wins),
        "live_total_pnl": pnl,
        "live_daily_pnl_hkt": round(
            sum(float(r.get("pnl") or 0) for r in today_rows if r.get("trade_result") in ("WIN", "LOSS")),
            2,
        ),
        "wallet_cash": round(wallet, 2),
        "starting_wallet_usdc": round(start, 2),
        "daily_loss_limit_usd": daily_loss_limit_usd(),
        "daily_loss_limit_hit": daily_loss_limit_hit(),
        "bot_instance": os.getenv("BOT_INSTANCE", "bot2"),
    }
