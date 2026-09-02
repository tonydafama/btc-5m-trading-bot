"""ETH 5m paper engine — Package 2+ aligned with BTC (fav/dog, US hours, skip_mid)."""

from __future__ import annotations

import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from bot_common import (
    ETH_LIVE_SESSION_FILE,
    ETH_LIVE_TRADE_LOG_FILE,
    ETH_TRADE_LOG_FILE,
    ETH_WINDOWS_FILE,
    LOG_DIR,
    build_equity_curve_with_labels,
    setup_logger,
)
from eth_strategy_rules import best_entry_checkpoint, eth_rules_preview
from new_strategy import (
    bought_label,
    calc_loss_pnl,
    calc_win_pnl,
    fetch_wallet_cash,
    now_hkt_str,
    resolved_label,
    side_to_contract,
    trade_result,
)
from package2_strategy import (
    daily_realized_pnl_hkt,
    evaluate_package2,
    is_package2_active,
    outside_trading_session,
    package2_config,
    record_closed_trade,
    risk_pause_status,
    session_pnl_snapshot,
    tier_max_ask,
    tiers_preview,
    trading_session_info,
    trading_session_skip_reason,
)
from polymarket_crypto import fetch_market_outcome

ETH_STRATEGY = "PACKAGE2+"
ETH_PAPER_SESSION_FILE = LOG_DIR / "eth_paper_session.json"


def eth_trading_mode() -> str:
    return os.getenv("ETH_TRADING_MODE", "paper").lower()


def load_eth_live_log() -> list[dict]:
    return _load_json(ETH_LIVE_TRADE_LOG_FILE, [])


def save_eth_live_log(rows: list[dict]) -> None:
    _save_json(ETH_LIVE_TRADE_LOG_FILE, rows)


def load_eth_session() -> dict:
    if eth_trading_mode() == "live":
        return _load_json(ETH_LIVE_SESSION_FILE, {})
    return load_eth_paper_session()


def load_active_eth_log() -> list[dict]:
    if eth_trading_mode() == "live":
        return load_eth_live_log()
    return load_eth_log()


def save_active_eth_log(rows: list[dict]) -> None:
    if eth_trading_mode() == "live":
        save_eth_live_log(rows)
    else:
        save_eth_log(rows)


def _append_active_eth_log(row: dict) -> None:
    rows = load_active_eth_log()
    rows.append(row)
    save_active_eth_log(rows)


def _update_last_active_eth_row(row: dict) -> list[dict]:
    rows = load_active_eth_log()
    if rows:
        rows[-1] = row
    return rows


def eth_daily_loss_limit_hit(rows: list[dict]) -> bool:
    from package2_strategy import (
        daily_loss_limit_hit,
        v1_combined_daily_loss_limit_hit,
        v1_combined_only_daily_stop,
    )

    if v1_combined_daily_loss_limit_hit():
        return True
    if v1_combined_only_daily_stop():
        return False
    return daily_loss_limit_hit(rows, asset="eth")


def _eth_bankroll_snapshot() -> dict[str, float]:
    wallet = fetch_wallet_cash(live=True)
    open_stakes = sum(
        float(r.get("stake") or 0)
        for r in load_active_eth_log()
        if r.get("entered") and r.get("status") == "open"
    )
    available = max(0.0, round(wallet - open_stakes, 2))
    return {"wallet_cash": round(wallet, 2), "bankroll": available, "open_stakes": round(open_stakes, 2)}


def _eth_slug_logged(slug: str) -> bool:
    sess = load_eth_session().get("session_id")
    for r in load_active_eth_log():
        if r.get("slug") != slug:
            continue
        if sess and r.get("session_id") and r.get("session_id") != sess:
            continue
        return True
    return False


def _load_json(path, default):
    if not path.exists():
        return default
    with path.open() as f:
        return json.load(f)


def _save_json(path, data) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def load_eth_log() -> list[dict]:
    return _load_json(ETH_TRADE_LOG_FILE, [])


def save_eth_log(rows: list[dict]) -> None:
    _save_json(ETH_TRADE_LOG_FILE, rows)


def load_eth_paper_session() -> dict:
    return _load_json(ETH_PAPER_SESSION_FILE, {})


def _timing_ok(snapshot: dict) -> bool:
    skip = snapshot.get("skip_reasons") or []
    blockers = [r for r in skip if not r.startswith("ask_")]
    return snapshot.get("in_entry_window", True) and not blockers


def _session_blocked_tier() -> dict[str, Any]:
    return {
        "entered": False,
        "stake": 0.0,
        "multiplier": "0x",
        "tier": "skip",
        "strategy_reason": trading_session_skip_reason(asset="eth"),
        "skip_category": "outside_trading_period",
    }


def _window_decision_logged(slug: str) -> bool:
    sess = load_eth_session().get("session_id")
    for r in load_active_eth_log():
        if r.get("slug") != slug:
            continue
        if (r.get("strategy") or "").upper() not in (ETH_STRATEGY, "PACKAGE2+"):
            continue
        if sess and r.get("session_id") and r.get("session_id") != sess:
            continue
        return True
    return False


def _filter_session_rows(rows: list[dict]) -> list[dict]:
    sess = load_eth_session().get("session_id")
    out = [r for r in rows if (r.get("strategy") or "").upper() in (ETH_STRATEGY, "PACKAGE2+")]
    if sess:
        out = [r for r in out if r.get("session_id") == sess]
    return out


def _pick_snap_for_log(checkpoints: list[dict]) -> dict | None:
    best = best_entry_checkpoint(checkpoints)
    if best:
        return best
    in_window = [c for c in checkpoints if c.get("in_entry_window")]
    if not in_window:
        return checkpoints[-1] if checkpoints else None
    with_ask = [c for c in in_window if c.get("ask") is not None]
    if not with_ask:
        return in_window[0]
    return min(with_ask, key=lambda c: c.get("ask") or 999)


def evaluate_eth_window_counterfactual(snapshot: dict) -> dict[str, Any]:
    """What Package 2+ would do if US session hours did not apply."""
    ask = snapshot.get("ask")
    if ask is None or ask <= 0:
        return {"would_enter": False, "reason": "no ask"}
    if not _timing_ok(snapshot):
        return {
            "would_enter": False,
            "reason": "timing/filter",
            "skip_reasons": snapshot.get("skip_reasons"),
        }
    tier = evaluate_package2(float(ask), snapshot=snapshot, ignore_session=True, asset="eth")
    would = bool(tier.get("entered"))
    return {
        "would_enter": would,
        "tier": tier.get("tier"),
        "stake": tier.get("stake"),
        "multiplier": tier.get("multiplier"),
        "strategy_reason": tier.get("strategy_reason"),
        "checkpoint": snapshot.get("checkpoint"),
        "t_remaining": snapshot.get("t_remaining"),
        "ask": ask,
        "side": snapshot.get("side"),
    }


def _build_eth_log_row(
    *,
    slug: str,
    title: str,
    side: str,
    ask: float | None,
    snapshot: dict,
    tier: dict,
    entered: bool,
    stake: float,
) -> dict:
    contract = side_to_contract(side) if entered else "NONE"
    result = trade_result("PENDING", contract, entered)
    sess = load_eth_session()
    mode = eth_trading_mode()
    return {
        "timestamp_hkt": now_hkt_str(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "asset": "ETH",
        "strategy": ETH_STRATEGY,
        "mode": mode,
        "session_id": sess.get("session_id"),
        "slug": slug,
        "market": title,
        "bought_contract": contract if entered else "NONE",
        "bought_label": bought_label(contract, ask, entered),
        "entry_ask": round(float(ask), 4) if ask is not None else None,
        "side": side,
        "stake": round(stake, 2),
        "multiplier": tier.get("multiplier", "0x"),
        "tier": tier.get("tier"),
        "entered": entered,
        "resolved_outcome": "PENDING",
        "resolved_label": resolved_label("PENDING", contract),
        "trade_result": result,
        "pnl": 0.0,
        "strategy_reason": tier.get("strategy_reason", ""),
        "skip_category": tier.get("skip_category"),
        "trading_session": trading_session_info(asset="eth"),
        "checkpoint": snapshot.get("checkpoint"),
        "t_remaining": snapshot.get("t_remaining"),
        "eth_move_usd": snapshot.get("delta_usd"),
        "eth_delta_pct": snapshot.get("delta_pct"),
        "open_eth_price": snapshot.get("open_eth_price"),
        "status": "open" if entered else "skipped",
    }


def try_eth_paper_entry(
    slug: str,
    title: str,
    snapshot: dict,
    open_eth_price: float,
) -> Optional[dict]:
    """One Package 2+ decision per ETH window → active trade log (+ CLOB if live)."""
    if _window_decision_logged(slug):
        return None
    from bot_control import is_bot_paused

    if is_bot_paused("eth"):
        return None

    side = snapshot.get("side", "up")
    ask = snapshot.get("ask")
    timing_ok = _timing_ok(snapshot)
    snapshot = {**snapshot, "open_eth_price": open_eth_price}
    bankroll_snap = _eth_bankroll_snapshot()

    if is_package2_active() and outside_trading_session(asset="eth"):
        tier = _session_blocked_tier()
        entered = False
    elif ask is None or ask <= 0:
        tier = {
            "entered": False,
            "stake": 0.0,
            "multiplier": "0x",
            "tier": "skip",
            "strategy_reason": "Skipped: no ask",
            "skip_category": "no_ask",
        }
        entered = False
    elif not timing_ok:
        tier = {
            "entered": False,
            "stake": 0.0,
            "multiplier": "0x",
            "tier": "skip",
            "strategy_reason": f"Skipped: timing/filter — {', '.join(snapshot.get('skip_reasons') or [])}",
            "skip_category": "timing_filter",
        }
        entered = False
    else:
        tier = evaluate_package2(float(ask), snapshot=snapshot, asset="eth")
        entered = bool(tier.get("entered"))

    active_rows = _filter_session_rows(load_active_eth_log())
    if entered and is_package2_active():
        from package2_strategy import (
            enforce_v1_combined_loss_stop,
            v1_combined_daily_loss_limit_hit,
            v1_combined_daily_loss_limit_usd,
        )

        if v1_combined_daily_loss_limit_hit():
            enforce_v1_combined_loss_stop()
            tier = {
                "entered": False,
                "stake": 0.0,
                "multiplier": "0x",
                "tier": "skip",
                "strategy_reason": (
                    f"Skipped: V1 combined daily loss limit "
                    f"−${v1_combined_daily_loss_limit_usd():.0f} reached (BTC+ETH)"
                ),
                "skip_category": "daily_loss_limit",
            }
            entered = False
        elif eth_daily_loss_limit_hit(active_rows):
            tier = {
                "entered": False,
                "stake": 0.0,
                "multiplier": "0x",
                "tier": "skip",
                "strategy_reason": "Skipped: daily loss limit reached",
                "skip_category": "daily_loss_limit",
            }
            entered = False

    stake = float(tier.get("stake") or 0) if entered else 0.0
    if entered and stake > bankroll_snap["bankroll"]:
        tier = {
            "entered": False,
            "stake": 0.0,
            "multiplier": "0x",
            "tier": "skip",
            "strategy_reason": (
                f"Skipped: insufficient bankroll ${bankroll_snap['bankroll']:.2f} < ${stake:.2f} "
                f"(wallet ${bankroll_snap['wallet_cash']:.2f})"
            ),
            "skip_category": "insufficient_bankroll",
        }
        entered = False
        stake = 0.0

    row = _build_eth_log_row(
        slug=slug,
        title=title,
        side=side,
        ask=float(ask) if ask is not None else None,
        snapshot=snapshot,
        tier=tier,
        entered=entered,
        stake=stake,
    )
    if entered:
        row["bankroll_after"] = round(max(0.0, bankroll_snap["bankroll"] - stake), 2)
        row["wallet_cash"] = bankroll_snap["wallet_cash"]
        row["status"] = "open"

    _append_active_eth_log(row)

    if entered and eth_trading_mode() == "live":
        row = _execute_eth_live_order(row, slug, side, ask, stake, tier, snapshot, bankroll_snap["bankroll"])
        save_active_eth_log(_update_last_active_eth_row(row))
    elif entered:
        setup_logger().info(
            "ETH PAPER %s %s @ %.2f stake $%.2f (%s)",
            slug, side.upper(), float(ask or 0), stake, tier.get("multiplier"),
        )
    return row


def _execute_eth_live_order(
    row: dict,
    slug: str,
    side: str,
    ask: float | None,
    stake: float,
    tier: dict,
    snapshot: dict,
    bankroll: float,
) -> dict:
    from btc_clob import compute_max_fill_price, place_live_buy_confirmed
    from paper_engine import _apply_live_fill, _mark_live_no_fill
    from polymarket_crypto import fetch_event_by_slug, parse_market

    live_row = dict(row)
    live_row["mode"] = "live"
    try:
        event = fetch_event_by_slug(slug)
        if not event:
            raise RuntimeError(f"No event for slug {slug}")
        market = parse_market(event, "eth")
        if not market:
            raise RuntimeError("Could not parse ETH market")
        token_id = market.up_token_id if side.lower() == "up" else market.down_token_id
        mk = (event.get("markets") or [{}])[0]
        tick = str(mk.get("tickSize") or "0.01")
        neg_risk = bool(mk.get("negRisk", False))
        min_size = float(mk.get("orderMinSize") or 5)
        tier_name = str(tier.get("tier") or "mid")
        cap = tier_max_ask(tier_name, float(ask or 0))
        max_fill = compute_max_fill_price(float(ask or 0), cap)
        live_row["decision_ask"] = round(float(ask or 0), 4)
        live_row["max_fill_price"] = max_fill
        t_rem = float(snapshot.get("t_remaining") or 0)
        deadline = time.time() + max(12.0, min(35.0, t_rem - 15.0)) if t_rem > 15 else time.time() + 10.0
        order = place_live_buy_confirmed(
            token_id=token_id,
            decision_ask=float(ask or 0),
            stake_usd=stake,
            max_fill_price=max_fill,
            tick_size=tick,
            neg_risk=neg_risk,
            min_size=min_size,
            deadline_ts=deadline,
        )
        live_row["execution_path"] = order.get("execution_path")
        filled = _apply_live_fill(live_row, order, stake, bankroll)
        if filled:
            from paper_engine import _amount_float

            shares = _amount_float(order.get("takingAmount") or order.get("taking_amount"))
            spent = float(live_row["stake"])
            if shares > 0:
                live_row["fill_price"] = round(spent / shares, 4)
                live_row["entry_ask"] = live_row["fill_price"]
                contract = side_to_contract(side)
                live_row["bought_label"] = bought_label(contract, live_row["fill_price"], True)
            setup_logger().info(
                "ETH LIVE FILLED %s %s @ %.2f stake $%.2f (%s) order %s",
                slug, side.upper(), float(ask or 0), live_row["stake"],
                tier.get("multiplier"), live_row.get("order_id"),
            )
        else:
            live_row["entered"] = False
            live_row["stake"] = 0.0
            live_row["status"] = "unfilled"
            setup_logger().warning(
                "ETH LIVE NO_FILL %s %s @ %.2f intended $%.2f",
                slug, side.upper(), float(ask or 0), stake,
            )
    except Exception as exc:
        if live_row.get("filled") and live_row.get("order_id"):
            setup_logger().warning("ETH LIVE filled but post-process error %s: %s", slug, exc)
            return live_row
        live_row["trade_result"] = "NO_FILL" if "not filled" in str(exc).lower() else "FAILED"
        live_row["status"] = "unfilled" if live_row["trade_result"] == "NO_FILL" else "failed"
        if live_row["trade_result"] == "NO_FILL":
            _mark_live_no_fill(live_row, reason_suffix=str(exc))
        else:
            live_row["entered"] = False
            live_row["stake"] = 0.0
            live_row["strategy_reason"] = f"ETH LIVE order failed: {exc}"
        setup_logger().error("ETH LIVE order failed %s: %s", slug, exc)
    return live_row


def _eth_would_enter_snapshot(snapshot: dict, *, bankroll: float) -> tuple[bool, dict]:
    ask = snapshot.get("ask")
    if is_package2_active() and outside_trading_session(asset="eth"):
        return False, _session_blocked_tier()
    if ask is None or ask <= 0:
        return False, {"entered": False, "strategy_reason": "Skipped: no ask"}
    if not _timing_ok(snapshot):
        return False, {
            "entered": False,
            "strategy_reason": f"Skipped: timing/filter — {', '.join(snapshot.get('skip_reasons') or [])}",
        }
    tier = evaluate_package2(float(ask), snapshot=snapshot, asset="eth")
    if not tier.get("entered"):
        return False, tier
    stake = float(tier.get("stake") or 0)
    if stake > bankroll:
        return False, {
            "entered": False,
            "strategy_reason": f"Skipped: insufficient bankroll ${bankroll:.2f} < ${stake:.2f}",
        }
    if is_package2_active() and eth_daily_loss_limit_hit(_filter_session_rows(load_active_eth_log())):
        return False, {"entered": False, "strategy_reason": "Skipped: daily loss limit reached"}
    return True, tier


def try_eth_live_entry_if_ready(
    slug: str,
    title: str,
    snapshot: dict,
    open_eth_price: float,
) -> Optional[dict]:
    """Fire live CLOB entry during the entry window (mirrors BTC observer)."""
    if eth_trading_mode() != "live":
        return None
    if _eth_slug_logged(slug):
        return None
    if is_package2_active() and outside_trading_session(asset="eth"):
        return None
    snap = _eth_bankroll_snapshot()
    ok, _tier = _eth_would_enter_snapshot(snapshot, bankroll=snap["bankroll"])
    if not ok:
        return None
    return try_eth_paper_entry(slug, title, snapshot, open_eth_price)


def log_eth_window_decision(
    slug: str,
    title: str,
    checkpoints: list[dict],
    open_eth_price: float,
) -> Optional[dict]:
    """Always write one trade-log row per ETH window."""
    if _window_decision_logged(slug):
        return None
    snap = _pick_snap_for_log(checkpoints)
    if not snap:
        snap = {
            "side": "up",
            "ask": None,
            "in_entry_window": False,
            "skip_reasons": ["no_checkpoint"],
            "checkpoint": None,
            "t_remaining": None,
        }
    return try_eth_paper_entry(slug, title, snap, open_eth_price)


def backfill_eth_window_decisions(limit: int = 40) -> int:
    if not ETH_WINDOWS_FILE.exists():
        return 0
    try:
        windows = json.loads(ETH_WINDOWS_FILE.read_text())
    except json.JSONDecodeError:
        return 0
    added = 0
    for rec in windows[-limit:]:
        slug = rec.get("slug", "")
        if not slug or _window_decision_logged(slug):
            continue
        row = log_eth_window_decision(
            slug,
            rec.get("title", ""),
            rec.get("checkpoints") or [],
            rec.get("open_eth_price") or 0.0,
        )
        if row:
            added += 1
    return added


def settle_eth_window(slug: str, outcome: str, *, source: str = "polymarket") -> Optional[dict]:
    res = "UP" if outcome.lower() == "up" else "DOWN"
    rows = load_active_eth_log()
    settled = None
    for row in rows:
        if row.get("slug") != slug:
            continue
        if row.get("resolved_outcome") not in ("PENDING", "NONE", "", None):
            continue
        contract = row.get("bought_contract", "NONE")
        row["resolved_outcome"] = res
        row["resolved_label"] = resolved_label(res, contract if row.get("entered") else "NONE")
        row["outcome_source"] = source
        row["settled_at"] = datetime.now(timezone.utc).isoformat()
        if row.get("entered") and row.get("status") == "open":
            stake = float(row.get("stake", 0))
            ask = float(row.get("entry_ask") or 0)
            result = trade_result(res, contract, True)
            pnl = calc_win_pnl(stake, ask) if result == "WIN" else calc_loss_pnl(stake)
            row["trade_result"] = result
            row["pnl"] = pnl
            row["status"] = "closed"
            setup_logger().info(
                "ETH SETTLE %s %s → %s pnl $%+.2f",
                slug,
                contract,
                res,
                pnl,
            )
            if row.get("tier"):
                record_closed_trade(tier=str(row["tier"]), won=result == "WIN", asset="eth")
        settled = row
    if settled:
        save_active_eth_log(rows)
        if settled.get("entered") and settled.get("trade_result") in ("WIN", "LOSS"):
            log_eth_session_pnl(f"settle {slug}")
            try:
                from backtest_research import append_rows_to_tape

                append_rows_to_tape([settled], asset="ETH", source="eth_live")
            except Exception:
                pass
    return settled


def settle_eth_from_polymarket(slug: str) -> Optional[dict]:
    resolution = fetch_market_outcome(slug)
    if not resolution:
        return None
    return settle_eth_window(slug, resolution["outcome"], source="polymarket")


def settle_stale_eth_trades() -> int:
    count = 0
    now = time.time()
    seen: set[str] = set()
    for row in load_active_eth_log():
        if row.get("resolved_outcome") not in ("PENDING", "NONE", "", None):
            continue
        slug = row.get("slug", "")
        if not slug or slug in seen:
            continue
        try:
            start = int(slug.rsplit("-", 1)[-1])
        except ValueError:
            continue
        if now < start + 330:
            continue
        if settle_eth_from_polymarket(slug):
            count += 1
            seen.add(slug)
    return count


def _session_rows(rows: list[dict]) -> list[dict]:
    return _filter_session_rows(rows)


def log_eth_session_pnl(context: str = "") -> None:
    snap = session_pnl_snapshot(_session_rows(load_active_eth_log()))
    label = "ETH LIVE PnL" if eth_trading_mode() == "live" else "ETH bot PnL"
    if context:
        label = f"{label} · {context}"
    setup_logger().info(
        "%s · today $%+.2f · overall $%+.2f",
        label,
        snap["daily_pnl_hkt"],
        snap["overall_pnl"],
    )


def eth_summary(*, live_wallet: bool = True) -> dict[str, Any]:
    rows = _session_rows(load_active_eth_log())
    entered = [r for r in rows if r.get("entered")]
    skipped = [r for r in rows if not r.get("entered")]
    closed = [r for r in entered if r.get("trade_result") in ("WIN", "LOSS")]
    open_rows = [r for r in entered if r.get("trade_result") == "PENDING"]
    wins = [r for r in closed if r.get("trade_result") == "WIN"]
    pnl_snap = session_pnl_snapshot(rows)
    paper_pnl = pnl_snap["overall_pnl"]
    daily_paper_pnl = pnl_snap["daily_pnl_hkt"]

    sess = load_eth_session()
    mode = eth_trading_mode()
    wallet = fetch_wallet_cash(live=live_wallet)
    start = float(sess.get("starting_wallet_usdc") or sess.get("starting_bankroll") or 0)
    if start <= 0:
        start = round(wallet, 2)

    from bot_common import account_pnl_snapshot

    acct = account_pnl_snapshot(live_wallet=live_wallet) if mode == "live" else {}
    account_pnl = acct.get("account_pnl") if mode == "live" else round(wallet - start, 2)
    account_pnl_pct = acct.get("account_pnl_pct") if mode == "live" else (
        round(account_pnl / start * 100, 2) if start else 0
    )
    total_pnl = account_pnl if account_pnl is not None else round(wallet - start, 2)
    equity = round(wallet, 2)

    curve, curve_labels = build_equity_curve_with_labels(
        start,
        closed,
        session_start_hkt=sess.get("started_at_hkt"),
        pad_to=equity if len(closed) == 0 else None,
    )

    rules = eth_rules_preview()
    p2 = tiers_preview("eth")
    open_stakes = round(sum(float(r.get("stake") or 0) for r in open_rows), 2)
    return {
        "asset": "ETH",
        "strategy": ETH_STRATEGY,
        "mode": mode,
        "wallet_cash": equity,
        "starting_wallet_usdc": start,
        "starting_bankroll": start,
        "paper_pnl": paper_pnl,
        "overall_pnl": paper_pnl,
        "trade_log_pnl": paper_pnl,
        "account_pnl": account_pnl,
        "account_pnl_pct": account_pnl_pct,
        "trade_log_pnl_pct": round(paper_pnl / start * 100, 2) if start else 0,
        "daily_pnl_hkt": daily_paper_pnl,
        "daily_paper_pnl_hkt": daily_paper_pnl,
        "total_pnl": total_pnl,
        "total_pnl_pct": round(total_pnl / start * 100, 2) if start else 0,
        "equity": equity,
        "open_stakes": open_stakes,
        "trades_entered": len(entered),
        "trades_skipped": len(skipped),
        "trades_total": len(rows),
        "trades_closed": len(closed),
        "trades_open": len(open_rows),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "equity_curve": curve,
        "equity_curve_labels": curve_labels,
        "dynamic_tiers": p2,
        "package2": p2,
        "risk_pause": risk_pause_status(asset="eth") if is_package2_active() else None,
        "eth_rules": rules,
        "live_session": sess,
        "observation": {
            "windows_judged": len(rows),
            "windows_entered": len(entered),
            "windows_skipped": len(skipped),
            "windows_closed": len(closed),
        },
    }
