"""Paper / live trading — split logs: trade_log_paper.json + trade_log_live.json."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv("/root/btc-5m-bot/.env")

from bot_common import (
    LIVE_SESSION_FILE,
    LIVE_TRADE_LOG_FILE,
    LOG_DIR,
    NEW_STATE_FILE,
    PAPER_TRADE_LOG_FILE,
    TRADE_LOG_FILE,
    build_equity_curve_with_labels,
    read_window_stats,
    setup_logger,
)
from new_strategy import (
    bought_label,
    calc_loss_pnl,
    calc_win_pnl,
    effective_bankroll,
    evaluate_ask_tier,
    load_config,
    now_hkt_str,
    resolved_label,
    side_to_contract,
    simulate_old_for_side,
    strategy_started,
    trade_result,
    trading_mode,
)

from package2_strategy import (
    daily_loss_limit_hit,
    daily_realized_pnl_hkt,
    enforce_v1_combined_loss_stop,
    evaluate_package2,
    favorite_paused,
    global_paused,
    is_package2_active,
    outside_trading_session,
    package2_config,
    record_closed_trade,
    risk_pause_status,
    session_pnl_snapshot,
    tiers_preview as package2_tiers_preview,
    trading_session_info,
    trading_session_skip_reason,
    underdog_paused,
    v1_combined_daily_loss_limit_hit,
    v1_combined_daily_loss_limit_usd,
)

OLD_BET = 5.0


def current_strategy_name() -> str:
    return load_config().get("current_strategy", "NEW").upper()


def _filter_strategy_rows(rows: list[dict]) -> list[dict]:
    strat = current_strategy_name()
    out = [r for r in rows if (r.get("strategy") or "NEW").upper() == strat]
    if trading_mode() == "live":
        sess = load_live_session().get("session_id")
        if sess:
            out = [r for r in out if r.get("session_id") == sess]
    return out


def _session_trade_rows() -> list[dict]:
    if trading_mode() == "live":
        return _filter_strategy_rows(load_live_log())
    return _filter_strategy_rows(load_paper_log())


def log_session_pnl(context: str = "") -> None:
    snap = session_pnl_snapshot(_session_trade_rows())
    label = "BTC bot PnL"
    if context:
        label = f"{label} · {context}"
    setup_logger().info(
        "%s · today $%+.2f · overall $%+.2f",
        label,
        snap["daily_pnl_hkt"],
        snap["overall_pnl"],
    )


def _amount_float(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def order_is_filled(order: dict | None, *, fetched: dict | None = None) -> bool:
    """True only when CLOB confirms USDC spent or shares matched."""
    for src in (fetched, order):
        if not src:
            continue
        if _amount_float(src.get("makingAmount") or src.get("making_amount")) > 0:
            return True
        if _amount_float(src.get("takingAmount") or src.get("taking_amount")) > 0:
            return True
        matched = src.get("size_matched") or src.get("sizeMatched")
        if matched is not None and _amount_float(matched) > 0:
            return True
        status = str(src.get("status") or "").upper()
        if status in ("MATCHED", "FILLED"):
            return True
    return False


def actual_stake_usd(order: dict | None, fallback: float) -> float:
    """USDC spent on a matched buy — prefer CLOB makingAmount over price×size estimate."""
    if not order:
        return round(fallback, 2)
    raw = order.get("makingAmount") or order.get("making_amount")
    if raw is not None and str(raw).strip():
        try:
            spent = round(float(raw), 2)
            if spent > 0:
                return spent
        except (TypeError, ValueError):
            pass
    est = round(float(order.get("stake_usd") or fallback), 2)
    return est if est > 0 else 0.0


def live_min_fill_ratio() -> float:
    """Minimum fraction of intended stake required — matches V1 FOK all-or-nothing behavior."""
    return float(os.getenv("LIVE_MIN_FILL_RATIO", "0.99"))


def live_fill_meets_intended(actual: float, intended: float) -> bool:
    if intended <= 0:
        return actual > 0
    return actual >= round(intended * live_min_fill_ratio(), 2) - 0.01


def _mark_live_no_fill(row: dict, *, reason_suffix: str = "") -> None:
    base = row.get("strategy_reason") or ""
    row["filled"] = False
    row["entered"] = False
    row["stake"] = 0.0
    row["trade_result"] = "NO_FILL"
    row["pnl"] = 0.0
    row["status"] = "unfilled"
    row["strategy_reason"] = f"{base} — {reason_suffix}".strip(" —")


def _apply_live_fill(row: dict, order: dict, intended_stake: float, bankroll: float) -> bool:
    from btc_clob import fetch_order_status

    order_id = order.get("orderID") or order.get("order_id")
    row["live_order"] = order
    row["order_id"] = order_id
    row["stake_intended"] = round(intended_stake, 2)

    fetched: dict | None = None
    if order.get("fill_confirmed"):
        filled = order_is_filled(order)
    else:
        filled = order_is_filled(order)
        if not filled and order_id:
            time.sleep(1.5)
            fetched = fetch_order_status(order_id)
            if fetched:
                row["live_order_fetched"] = fetched
                filled = order_is_filled(order, fetched=fetched)

    if not filled:
        _mark_live_no_fill(row, reason_suffix="order NOT filled (0 matched on CLOB)")
        row["bankroll_after"] = round(bankroll, 2)
        return False

    merged = {**(order or {}), **(fetched or {})}
    actual = actual_stake_usd(merged, intended_stake)
    if not live_fill_meets_intended(actual, intended_stake):
        pct = int(live_min_fill_ratio() * 100)
        _mark_live_no_fill(
            row,
            reason_suffix=(
                f"partial fill ${actual:.2f} vs intended ${intended_stake:.2f} "
                f"(need ≥{pct}% — use FOK like V1)"
            ),
        )
        row["stake_partial_usd"] = actual
        row["bankroll_after"] = round(bankroll, 2)
        return False

    row["filled"] = True
    row["stake"] = actual
    row["bankroll_after"] = round(max(0.0, bankroll - row["stake"]), 2)
    try:
        from trade_analytics import enrich_fill_metrics

        enrich_fill_metrics(row, order=merged)
        row.setdefault("data_schema_version", 1)
    except Exception:
        pass
    return True


def repair_live_log_stakes(*, force: bool = False) -> int:
    """Reconcile live rows with CLOB fill state and makingAmount."""
    import time

    from btc_clob import fetch_order_status

    global _repair_last_run
    if not force:
        interval = float(os.getenv("LIVE_REPAIR_INTERVAL_SEC", "120"))
        if time.time() - _repair_last_run < interval:
            return 0

    sess = load_live_session().get("session_id")
    rows = load_live_log()
    fixed = 0
    for row in rows:
        if sess and row.get("session_id") and row.get("session_id") != sess:
            continue
        order_id = row.get("order_id") or (row.get("live_order") or {}).get("orderID")
        if not order_id:
            continue
        if row.get("live_order_fetched") and row.get("trade_result") in ("WIN", "LOSS", "NO_FILL", "FAILED", "SKIPPED"):
            continue
        fetched = fetch_order_status(order_id)
        filled = order_is_filled(row.get("live_order"), fetched=fetched)
        if fetched:
            row["live_order_fetched"] = fetched

        if not filled:
            if row.get("trade_result") in ("WIN", "LOSS", "PENDING") or row.get("entered"):
                _mark_live_no_fill(row, reason_suffix="repaired: CLOB shows 0 fill")
                fixed += 1
            continue

        row["filled"] = True
        merged = {**(row.get("live_order") or {}), **(fetched or {})}
        actual = actual_stake_usd(merged, float(row.get("stake") or row.get("stake_intended") or 0))
        intended = float(row.get("stake_intended") or row.get("stake") or 0)
        if actual <= 0:
            _mark_live_no_fill(row, reason_suffix="repaired: zero stake on CLOB")
            fixed += 1
            continue
        if not live_fill_meets_intended(actual, intended):
            _mark_live_no_fill(row, reason_suffix="repaired: partial fill below intended")
            row["stake_partial_usd"] = actual
            fixed += 1
            continue

        changed = False
        if actual != float(row.get("stake") or 0):
            row["stake"] = actual
            changed = True
        ask = float(row.get("entry_ask") or 0)
        result = row.get("trade_result")
        if result == "WIN" and ask:
            new_pnl = calc_win_pnl(actual, ask)
            if new_pnl != float(row.get("pnl") or 0):
                row["pnl"] = new_pnl
                changed = True
        elif result == "LOSS":
            new_pnl = calc_loss_pnl(actual)
            if new_pnl != float(row.get("pnl") or 0):
                row["pnl"] = new_pnl
                changed = True
        if changed:
            fixed += 1
    if fixed:
        save_live_log(rows)
        _reconcile_live_state_counters()
    _repair_last_run = time.time()
    return fixed


_repair_last_run: float = 0.0


def _reconcile_live_state_counters() -> None:
    if trading_mode() != "live":
        return
    rows = _filter_strategy_rows(load_live_log())
    entered = len([r for r in rows if r.get("entered")])
    skipped = len([r for r in rows if not r.get("entered") and r.get("trade_result") != "NO_FILL"])
    state = load_new_state()
    state["trades_entered"] = entered
    state["trades_skipped"] = skipped
    save_new_state(state)


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


def _starting_bankroll() -> float:
    return effective_bankroll()["bankroll"]


def _paper_pnl() -> float:
    rows = load_active_strategy_log()
    return sum(
        float(r.get("pnl") or 0)
        for r in rows
        if r.get("trade_result") in ("WIN", "LOSS")
    )


def _open_stakes() -> float:
    rows = load_active_strategy_log()
    return sum(
        float(r.get("stake") or 0)
        for r in rows
        if r.get("entered") and r.get("filled", True) and r.get("status") == "open"
    )


def _bankroll_snapshot(*, live_wallet: bool = True) -> dict[str, float]:
    return effective_bankroll(
        open_stakes=_open_stakes(),
        paper_pnl=_paper_pnl(),
        live_wallet=live_wallet,
    )


def load_new_state(*, live_wallet: bool = False) -> dict:
    snap = _bankroll_snapshot(live_wallet=live_wallet)
    default = {
        "strategy": "NEW",
        "dynamic_bankroll": True,
        "wallet_cash": snap["wallet_cash"],
        "bankroll": snap["bankroll"],
        "trades_entered": 0,
        "trades_skipped": 0,
    }
    state = _load_json(NEW_STATE_FILE, default)
    state["wallet_cash"] = snap["wallet_cash"]
    state["bankroll"] = snap["bankroll"]
    state["dynamic_bankroll"] = True
    return state


def save_new_state(state: dict) -> None:
    _save_json(NEW_STATE_FILE, state)


def load_paper_log() -> list[dict]:
    """All windows + OLD strategy comparison (continues in live mode)."""
    if PAPER_TRADE_LOG_FILE.exists():
        return _load_json(PAPER_TRADE_LOG_FILE, [])
    if TRADE_LOG_FILE.exists():
        return _load_json(TRADE_LOG_FILE, [])
    return []


def save_paper_log(rows: list[dict]) -> None:
    _save_json(PAPER_TRADE_LOG_FILE, rows)


def load_live_log() -> list[dict]:
    return _load_json(LIVE_TRADE_LOG_FILE, [])


def save_live_log(rows: list[dict]) -> None:
    _save_json(LIVE_TRADE_LOG_FILE, rows)


def load_active_strategy_log() -> list[dict]:
    """NEW strategy stats: live log in live mode, paper log in paper mode."""
    if trading_mode() == "live":
        return load_live_log()
    return load_paper_log()


def load_trade_log() -> list[dict]:
    """Backward-compatible alias for active strategy log."""
    return load_active_strategy_log()


def save_trade_log(rows: list[dict]) -> None:
    if trading_mode() == "live":
        save_live_log(rows)
    else:
        save_paper_log(rows)


def _window_decision_logged(slug: str) -> bool:
    """True if this window already has a trade-log decision row (live or paper)."""
    strat = current_strategy_name()
    sess = load_live_session().get("session_id")
    for loader in (load_live_log, load_paper_log):
        for r in loader():
            if r.get("slug") != slug or (r.get("strategy") or "NEW").upper() != strat:
                continue
            if sess and r.get("session_id") and r.get("session_id") != sess:
                continue
            return True
    return False


def dedupe_session_trade_logs(session_id: str | None = None) -> tuple[int, int]:
    """Keep one row per slug per session in live + paper logs (prefer entered trades)."""
    sess = session_id or load_live_session().get("session_id")
    if not sess:
        return 0, 0

    def _dedupe(rows: list[dict]) -> tuple[list[dict], int]:
        kept: dict[str, dict] = {}
        order: list[str] = []
        removed = 0
        passthrough: list[dict] = []
        for r in rows:
            if r.get("session_id") != sess:
                passthrough.append(r)
                continue
            slug = r.get("slug") or ""
            if not slug:
                passthrough.append(r)
                continue
            if slug not in kept:
                kept[slug] = r
                order.append(slug)
                continue
            removed += 1
            prev = kept[slug]
            if r.get("entered") and not prev.get("entered"):
                kept[slug] = r
            elif prev.get("entered") and not r.get("entered"):
                pass
            else:
                kept[slug] = r
        return passthrough + [kept[s] for s in order], removed

    live = load_live_log()
    live_out, live_rm = _dedupe(live)
    paper = load_paper_log()
    paper_out, paper_rm = _dedupe(paper)
    if live_rm:
        save_live_log(live_out)
    if paper_rm:
        save_paper_log(paper_out)
    return live_rm, paper_rm


def _slug_logged(slug: str) -> bool:
    """Paper: one row per slug. Live: block only after order attempt or filled trade."""
    strat = current_strategy_name()
    sess = load_live_session().get("session_id")
    if trading_mode() == "live":
        for r in load_live_log():
            if r.get("slug") != slug or (r.get("strategy") or "NEW").upper() != strat:
                continue
            if sess and r.get("session_id") and r.get("session_id") != sess:
                continue
            if r.get("entered") and r.get("filled", True):
                return True
            if r.get("order_id") or r.get("trade_result") in ("NO_FILL", "FAILED"):
                return True
        return False
    for r in load_paper_log():
        if r.get("slug") != slug or (r.get("strategy") or "NEW").upper() != strat:
            continue
        if sess and r.get("session_id") and r.get("session_id") != sess:
            continue
        return True
    return False


def _session_blocked_tier() -> dict[str, Any]:
    return {
        "entered": False,
        "stake": 0.0,
        "multiplier": "0x",
        "tier": "skip",
        "bankroll_used": None,
        "strategy_reason": trading_session_skip_reason(asset="btc"),
        "skip_category": "outside_trading_period",
    }


def evaluate_window_counterfactual(snapshot: dict, *, bankroll: float | None = None) -> dict[str, Any]:
    """What Package 2+ would do on this snap if session hours did not apply (backtest)."""
    ask = snapshot.get("ask")
    if ask is None or ask <= 0:
        return {"would_enter": False, "reason": "no ask"}
    if not _timing_ok(snapshot):
        return {
            "would_enter": False,
            "reason": "timing/filter",
            "skip_reasons": snapshot.get("skip_reasons"),
        }
    br = bankroll if bankroll is not None else _bankroll_snapshot()["bankroll"]
    tier = evaluate_package2(float(ask), snapshot=snapshot, ignore_session=True)
    would = bool(tier.get("entered")) and float(tier.get("stake") or 0) <= br
    if tier.get("entered") and float(tier.get("stake") or 0) > br:
        would = False
        tier = {
            **tier,
            "entered": False,
            "strategy_reason": f"Skipped: insufficient bankroll ${br:.2f}",
        }
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


def _would_enter_snapshot(snapshot: dict, *, bankroll: float) -> tuple[bool, dict]:
    ask = snapshot.get("ask")
    if is_package2_active() and outside_trading_session(asset="btc"):
        return False, _session_blocked_tier()
    if ask is None or ask <= 0:
        return False, {"entered": False, "strategy_reason": "Skipped: no ask"}
    if not _timing_ok(snapshot):
        return False, {
            "entered": False,
            "strategy_reason": f"Skipped: timing/filter — {', '.join(snapshot.get('skip_reasons') or [])}",
        }
    tier = evaluate_package2(float(ask), snapshot=snapshot) if is_package2_active() else evaluate_ask_tier(float(ask), bankroll)
    if not tier.get("entered"):
        return False, tier
    stake = float(tier.get("stake") or 0)
    if stake > bankroll:
        return False, {
            "entered": False,
            "strategy_reason": f"Skipped: insufficient bankroll ${bankroll:.2f} < ${stake:.2f}",
        }
    if is_package2_active() and v1_combined_daily_loss_limit_hit():
        enforce_v1_combined_loss_stop()
        return False, {
            "entered": False,
            "strategy_reason": (
                f"Skipped: V1 combined daily loss limit "
                f"−${v1_combined_daily_loss_limit_usd():.0f} reached (BTC+ETH)"
            ),
        }
    if is_package2_active() and daily_loss_limit_hit(_filter_strategy_rows(load_active_strategy_log())):
        return False, {"entered": False, "strategy_reason": "Skipped: daily loss limit reached"}
    return True, tier


def load_live_session() -> dict[str, Any]:
    if LIVE_SESSION_FILE.exists():
        try:
            return json.loads(LIVE_SESSION_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _timing_ok(snapshot: dict) -> bool:
    skip = snapshot.get("skip_reasons") or []
    blockers = [r for r in skip if not r.startswith("ask_")]
    return snapshot.get("in_entry_window", True) and not blockers


def _build_log_row(
    *,
    slug: str,
    title: str,
    side: str,
    ask: float | None,
    snapshot: dict,
    tier: dict,
    entered: bool,
    stake: float,
    state: dict,
    resolved_outcome: str = "PENDING",
) -> dict:
    contract = side_to_contract(side) if entered else "NONE"
    mult = tier.get("multiplier", "0x")
    reason = tier.get("strategy_reason", "")

    res = resolved_outcome.upper() if resolved_outcome not in ("PENDING", "") else "PENDING"
    result = trade_result(res, contract, entered)
    pnl = 0.0
    if entered and result == "WIN" and ask:
        pnl = calc_win_pnl(stake, ask)
    elif entered and result == "LOSS":
        pnl = calc_loss_pnl(stake)

    bankroll = float(state["bankroll"])
    if entered and result == "PENDING":
        bankroll_after = round(bankroll, 2)
    elif entered and result == "WIN" and ask:
        bankroll_after = round(bankroll + stake + pnl, 2)
    elif entered:
        bankroll_after = round(bankroll, 2)
    else:
        bankroll_after = round(bankroll, 2)

    old_sim: dict = {}
    if not is_package2_active():
        old_sim = simulate_old_for_side(
            ask=ask,
            timing_ok=_timing_ok(snapshot),
            side=side,
            resolved_outcome=res,
        )
        if old_sim.get("would_enter") and res in ("UP", "DOWN") and ask:
            won = res == side_to_contract(side)
            old_sim["pnl"] = calc_win_pnl(OLD_BET, ask) if won else calc_loss_pnl(OLD_BET)

    strat = current_strategy_name()
    return {
        "timestamp_hkt": now_hkt_str(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": strat,
        "tier": tier.get("tier"),
        "slug": slug,
        "market": title,
        "bought_contract": contract if entered else "NONE",
        "bought_label": bought_label(contract, ask, entered),
        "entry_ask": round(ask, 4) if ask is not None else None,
        "side": side,
        "stake": round(stake, 2),
        "multiplier": mult,
        "entered": entered,
        "resolved_outcome": res,
        "resolved_label": resolved_label(res, contract),
        "trade_result": result,
        "pnl": pnl,
        "bankroll_after": bankroll_after,
        "bankroll_used": tier.get("bankroll_used"),
        "wallet_cash": state.get("wallet_cash"),
        "strategy_reason": reason,
        "skip_category": tier.get("skip_category"),
        "trading_session": trading_session_info(asset="btc"),
        "checkpoint": snapshot.get("checkpoint"),
        "t_remaining": snapshot.get("t_remaining"),
        "status": "open" if entered and result == "PENDING" else ("closed" if entered else "skipped"),
        "old_strategy_sim": old_sim if old_sim else None,
        "mode": "paper" if trading_mode() == "live" else trading_mode(),
        "session_id": load_live_session().get("session_id"),
    }


def try_trade_entry(
    slug: str,
    title: str,
    snapshot: dict,
    open_btc_price: float,
) -> Optional[dict]:
    """One NEW-strategy decision per window → trade_log_full.json (+ live CLOB if enabled)."""
    if not strategy_started():
        return None
    if _slug_logged(slug):
        return None
    from bot_control import is_bot_paused

    if is_bot_paused("btc"):
        return None

    side = snapshot.get("side", "up")
    ask = snapshot.get("ask")
    timing_ok = _timing_ok(snapshot)
    snap = _bankroll_snapshot()
    state = load_new_state()
    state.update(snap)
    if is_package2_active() and outside_trading_session(asset="btc"):
        tier = _session_blocked_tier()
        entered = False
    elif ask is None or ask <= 0:
        tier = {"entered": False, "stake": 0.0, "multiplier": "0x", "strategy_reason": "Skipped: no ask"}
        entered = False
    elif not timing_ok:
        tier = {
            "entered": False,
            "stake": 0.0,
            "multiplier": "0x",
            "tier": "skip",
            "bankroll_used": snap["bankroll"],
            "strategy_reason": f"Skipped: timing/filter — {', '.join(snapshot.get('skip_reasons') or [])}",
            "skip_category": "timing_filter",
        }
        entered = False
    else:
        tier = evaluate_package2(float(ask), snapshot=snapshot) if is_package2_active() else evaluate_ask_tier(float(ask), snap["bankroll"])
        entered = tier["entered"]

    if entered and is_package2_active() and v1_combined_daily_loss_limit_hit():
        enforce_v1_combined_loss_stop()
        tier = {
            "entered": False,
            "stake": 0.0,
            "multiplier": "0x",
            "tier": "skip",
            "bankroll_used": snap["bankroll"],
            "strategy_reason": (
                f"Skipped: V1 combined daily loss limit "
                f"−${v1_combined_daily_loss_limit_usd():.0f} reached (BTC+ETH)"
            ),
            "skip_category": "daily_loss_limit",
        }
        entered = False
    elif entered and is_package2_active() and daily_loss_limit_hit(_filter_strategy_rows(load_active_strategy_log())):
        tier = {
            "entered": False,
            "stake": 0.0,
            "multiplier": "0x",
            "tier": "skip",
            "bankroll_used": snap["bankroll"],
            "strategy_reason": "Skipped: daily loss limit reached",
        }
        entered = False

    stake = float(tier["stake"]) if entered else 0.0

    if entered and stake > snap["bankroll"]:
        tier = {
            "entered": False,
            "stake": 0.0,
            "multiplier": "0x",
            "bankroll_used": snap["bankroll"],
            "strategy_reason": (
                f"Skipped: insufficient bankroll ${snap['bankroll']:.2f} < ${stake:.2f} "
                f"(wallet ${snap['wallet_cash']:.2f})"
            ),
        }
        entered = False
        stake = 0.0

    if entered:
        state["trades_entered"] = int(state.get("trades_entered", 0)) + 1
    else:
        state["trades_skipped"] = int(state.get("trades_skipped", 0)) + 1

    row = _build_log_row(
        slug=slug, title=title, side=side,
        ask=float(ask) if ask is not None else None,
        snapshot=snapshot,
        tier=tier, entered=entered, stake=stake, state=state,
    )
    if entered:
        row["bankroll_after"] = round(max(0.0, snap["bankroll"] - stake), 2)

    save_new_state(state)
    _append_paper_log(row)

    mode = trading_mode()
    live_row: dict | None = None
    if mode == "live":
        live_row = dict(row)
        live_row["mode"] = "live"
        _append_live_log(live_row)

    if entered and mode == "live" and live_row is not None:
        try:
            from btc_clob import compute_max_fill_price, place_live_buy_confirmed
            from package2_strategy import package2_config, tier_max_ask
            from polymarket_btc import fetch_event_by_slug, parse_market

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
            p2 = package2_config()
            tier_name = str(tier.get("tier") or "mid")
            cap = tier_max_ask(tier_name, float(ask))
            max_fill = compute_max_fill_price(float(ask), cap)
            live_row["decision_ask"] = round(float(ask), 4)
            live_row["max_fill_price"] = max_fill
            t_rem = float(snapshot.get("t_remaining") or 0)
            deadline = time.time() + max(12.0, min(35.0, t_rem - 15.0)) if t_rem > 15 else time.time() + 10.0

            order = place_live_buy_confirmed(
                token_id=token_id,
                decision_ask=float(ask),
                stake_usd=stake,
                max_fill_price=max_fill,
                tick_size=tick,
                neg_risk=neg_risk,
                min_size=min_size,
                deadline_ts=deadline,
            )
            live_row["execution_path"] = order.get("execution_path")
            filled = _apply_live_fill(live_row, order, stake, snap["bankroll"])
            if filled:
                shares = _amount_float(order.get("takingAmount") or order.get("taking_amount"))
                spent = float(live_row["stake"])
                if shares > 0:
                    live_row["fill_price"] = round(spent / shares, 4)
                    live_row["entry_ask"] = live_row["fill_price"]
                    contract = side_to_contract(side)
                    live_row["bought_label"] = bought_label(contract, live_row["fill_price"], True)
            if not filled:
                state = load_new_state()
                state["trades_entered"] = max(0, int(state.get("trades_entered", 0)) - 1)
                state["trades_skipped"] = int(state.get("trades_skipped", 0)) + 1
                save_new_state(state)
            save_live_log(_update_last_live_row(live_row))
            if filled:
                setup_logger().info(
                    "%s LIVE FILLED %s %s @ %.2f stake $%.2f (%s) path=%s order %s",
                    current_strategy_name(), slug, side.upper(), ask, live_row["stake"],
                    tier["multiplier"], live_row.get("execution_path"), live_row.get("order_id"),
                )
            else:
                setup_logger().warning(
                    "%s LIVE NO_FILL %s %s @ %.2f intended $%.2f order %s",
                    current_strategy_name(), slug, side.upper(), ask, stake, live_row.get("order_id"),
                )
        except Exception as exc:
            if live_row.get("filled") and live_row.get("order_id"):
                setup_logger().warning(
                    "%s LIVE filled but post-process error %s: %s",
                    current_strategy_name(), slug, exc,
                )
                save_live_log(_update_last_live_row(live_row))
                return live_row
            live_row["trade_result"] = "NO_FILL" if "not filled" in str(exc).lower() or "above max fill" in str(exc).lower() else "FAILED"
            live_row["status"] = "unfilled" if live_row["trade_result"] == "NO_FILL" else "failed"
            if live_row["trade_result"] == "NO_FILL":
                _mark_live_no_fill(live_row, reason_suffix=str(exc))
                state = load_new_state()
                state["trades_entered"] = max(0, int(state.get("trades_entered", 0)) - 1)
                state["trades_skipped"] = int(state.get("trades_skipped", 0)) + 1
                save_new_state(state)
            else:
                live_row["entered"] = False
                live_row["stake"] = 0.0
                live_row["pnl"] = 0.0
                live_row["strategy_reason"] = f"LIVE order failed: {exc}"
                state = load_new_state()
                state["trades_entered"] = max(0, int(state.get("trades_entered", 0)) - 1)
                state["trades_skipped"] = int(state.get("trades_skipped", 0)) + 1
                save_new_state(state)
            setup_logger().error("%s LIVE order failed %s: %s", current_strategy_name(), slug, exc)
            save_live_log(_update_last_live_row(live_row))
            return live_row
    elif entered:
        setup_logger().info(
            "%s PAPER %s %s @ %.2f stake $%.2f (%s) bankroll $%.2f",
            current_strategy_name(), slug, side.upper(), ask, stake, tier["multiplier"], snap["bankroll"],
        )
    return live_row if (mode == "live" and live_row is not None) else row


def try_live_entry_if_ready(
    slug: str,
    title: str,
    snapshot: dict,
    open_btc_price: float,
) -> Optional[dict]:
    """Fire live CLOB entry during the entry window when rules pass (not at window end)."""
    if trading_mode() != "live" or not strategy_started():
        return None
    if _slug_logged(slug):
        return None
    if is_package2_active() and outside_trading_session(asset="btc"):
        return None
    snap = _bankroll_snapshot()
    ok, _tier = _would_enter_snapshot(snapshot, bankroll=snap["bankroll"])
    if not ok:
        return None
    return try_trade_entry(slug, title, snapshot, open_btc_price)


def log_window_trade_decision(
    slug: str,
    title: str,
    checkpoints: list[dict],
    open_btc_price: float,
) -> Optional[dict]:
    """Always write one trade-log row per window (live/paper) for backtest audit."""
    if not strategy_started() or _window_decision_logged(slug):
        return None
    snap = _pick_new_snap_for_log(checkpoints)
    if not snap:
        snap = {
            "side": "up",
            "ask": None,
            "in_entry_window": False,
            "skip_reasons": ["no_checkpoint"],
            "checkpoint": None,
            "t_remaining": None,
        }
    return try_trade_entry(slug, title, snap, open_btc_price)


def _pick_new_snap_for_log(checkpoints: list[dict]) -> dict | None:
    from strategy_rules import best_entry_checkpoint

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


def backfill_window_trade_decisions(limit: int = 40) -> int:
    """Write missing per-window trade log rows from windows.json (e.g. after observer errors)."""
    from bot_common import WINDOWS_FILE

    if not WINDOWS_FILE.exists():
        return 0
    try:
        windows = json.loads(WINDOWS_FILE.read_text())
    except json.JSONDecodeError:
        return 0
    added = 0
    for rec in windows[-limit:]:
        slug = rec.get("slug", "")
        if not slug or _window_decision_logged(slug):
            continue
        row = log_window_trade_decision(
            slug,
            rec.get("title", ""),
            rec.get("checkpoints") or [],
            rec.get("open_btc_price") or 0.0,
        )
        if row:
            added += 1
    return added


def backfill_live_log_from_paper() -> int:
    """Sync missing live-session rows from paper log (skipped windows before split fix)."""
    sess = load_live_session().get("session_id")
    if not sess:
        return 0
    live = load_live_log()
    seen = {r.get("slug") for r in live if r.get("slug")}
    added = 0
    for r in load_paper_log():
        if r.get("session_id") != sess:
            continue
        slug = r.get("slug", "")
        if not slug or slug in seen:
            continue
        lr = dict(r)
        lr["mode"] = "live"
        live.append(lr)
        seen.add(slug)
        added += 1
    if added:
        save_live_log(live)
    return added


def _update_last_log_row(row: dict) -> list[dict]:
    rows = load_active_strategy_log()
    if rows:
        rows[-1] = row
    return rows


def _update_last_live_row(row: dict) -> list[dict]:
    rows = load_live_log()
    if rows:
        rows[-1] = row
    return rows


def _update_last_paper_row(row: dict) -> list[dict]:
    rows = load_paper_log()
    if rows:
        rows[-1] = row
    return rows


try_paper_entry = try_trade_entry


def _append_paper_log(row: dict) -> None:
    rows = load_paper_log()
    rows.append(row)
    save_paper_log(rows)


def _append_live_log(row: dict) -> None:
    rows = load_live_log()
    rows.append(row)
    save_live_log(rows)


def _append_log(row: dict) -> None:
    _append_paper_log(row)


def settle_from_polymarket(slug: str) -> Optional[dict]:
    from polymarket_btc import fetch_market_outcome

    resolution = fetch_market_outcome(slug)
    if not resolution:
        return None
    return settle_window(slug, resolution["outcome"], source="polymarket")


def _refresh_old_sim(row: dict, res: str) -> None:
    """Recompute OLD comparison PnL once market outcome is known."""
    sim = row.get("old_strategy_sim") or {}
    if not sim.get("would_enter"):
        return
    ask = row.get("entry_ask")
    side = row.get("side", "up")
    row["old_strategy_sim"] = simulate_old_for_side(
        ask=ask,
        timing_ok=True,
        side=side,
        resolved_outcome=res,
    )


def _settle_rows(
    rows: list[dict],
    slug: str,
    res: str,
    *,
    source: str,
    settle_new_pnl: bool,
) -> tuple[list[dict], dict | None, bool]:
    settled = None
    state = load_new_state()
    changed = False

    strat = current_strategy_name()
    for row in rows:
        if row.get("slug") != slug or (row.get("strategy") or "NEW").upper() != strat:
            continue
        if row.get("resolved_outcome") not in ("PENDING", "NONE", "", None):
            continue

        contract = row.get("bought_contract", "NONE")
        now_iso = datetime.now(timezone.utc).isoformat()

        row["resolved_outcome"] = res
        row["resolved_label"] = resolved_label(res, contract if row.get("entered") else "NONE")
        row["outcome_source"] = source
        row["settled_at"] = now_iso
        _refresh_old_sim(row, res)

        if settle_new_pnl and row.get("entered") and row.get("status") == "open":
            if row.get("mode") == "live" and row.get("order_id") and row.get("filled") is not False:
                from btc_clob import fetch_order_status

                fetched = fetch_order_status(row["order_id"])
                if fetched:
                    row["live_order_fetched"] = fetched
                if not order_is_filled(row.get("live_order"), fetched=fetched):
                    _mark_live_no_fill(row, reason_suffix="settle: CLOB shows 0 fill")
                    changed = True
                    settled = row
                    continue

            if not row.get("filled", True):
                row["trade_result"] = "NO_FILL"
                row["pnl"] = 0.0
                row["status"] = "unfilled"
                changed = True
                settled = row
                continue

            stake = float(row.get("stake", 0))
            ask = float(row.get("entry_ask") or 0)
            result = trade_result(res, contract, True)
            pnl = calc_win_pnl(stake, ask) if result == "WIN" else calc_loss_pnl(stake)

            snap = _bankroll_snapshot()
            state.update(snap)
            state["bankroll"] = round(snap["bankroll"] + pnl, 2)

            row["trade_result"] = result
            row["pnl"] = pnl
            row["bankroll_after"] = round(float(state["bankroll"]), 2)
            row["status"] = "closed"
            try:
                from trade_analytics import enrich_v1_row

                enriched = enrich_v1_row(row)
                row.update(enriched)
            except Exception:
                pass
            save_new_state(state)
            if is_package2_active() and row.get("tier"):
                record_closed_trade(tier=str(row["tier"]), won=result == "WIN", asset="btc")
            setup_logger().info(
                "%s SETTLE %s %s → %s pnl $%+.2f bankroll $%.2f [%s]",
                strat, slug, contract, res, pnl, state["bankroll"], row.get("mode", "?"),
            )
        elif row.get("entered") and row.get("status") == "open":
            row["status"] = "closed"
            row["trade_result"] = "LIVE_REF"
            row["pnl"] = 0.0

        changed = True
        settled = row

    return rows, settled, changed


def settle_window(slug: str, outcome: str, *, source: str = "polymarket") -> Optional[dict]:
    res = "UP" if outcome.lower() == "up" else "DOWN"
    settled = None
    mode = trading_mode()

    paper_rows = load_paper_log()
    paper_rows, paper_settled, paper_changed = _settle_rows(
        paper_rows, slug, res, source=source, settle_new_pnl=(mode != "live"),
    )
    if paper_changed:
        save_paper_log(paper_rows)
        settled = paper_settled

    if mode == "live":
        live_rows = load_live_log()
        live_rows, live_settled, live_changed = _settle_rows(
            live_rows, slug, res, source=source, settle_new_pnl=True,
        )
        if live_changed:
            save_live_log(live_rows)
            if live_settled and live_settled.get("entered"):
                settled = live_settled

    if settled and settled.get("entered") and settled.get("trade_result") in ("WIN", "LOSS"):
        log_session_pnl(f"settle {slug}")
        try:
            from backtest_research import append_rows_to_tape

            append_rows_to_tape([settled], asset="BTC", source="btc_live")
        except Exception:
            pass

    return settled


def settle_stale_open_trades() -> int:
    """Backfill Polymarket outcomes for any log row still marked PENDING."""
    count = 0
    now = time.time()
    seen: set[str] = set()
    for row in load_paper_log() + load_live_log():
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
        if settle_from_polymarket(slug):
            count += 1
            seen.add(slug)
    return count


def _max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return round(max_dd, 2)


def new_strategy_summary(*, live_wallet: bool = True) -> dict[str, Any]:
    from new_strategy import dynamic_tiers_preview

    state = load_new_state(live_wallet=live_wallet)
    snap = _bankroll_snapshot(live_wallet=live_wallet)
    strat = current_strategy_name()
    rows = _filter_strategy_rows(load_active_strategy_log())
    paper_rows = _filter_strategy_rows(load_paper_log())
    entered = [r for r in rows if r.get("entered")]
    skipped = [r for r in rows if not r.get("entered") and r.get("trade_result") != "NO_FILL"]
    no_fill = [r for r in rows if r.get("trade_result") == "NO_FILL"]
    closed = [r for r in entered if r.get("trade_result") in ("WIN", "LOSS")]
    failed = [r for r in rows if r.get("trade_result") == "FAILED"]
    wins = [r for r in closed if r.get("trade_result") == "WIN"]
    open_rows = [r for r in entered if r.get("trade_result") == "PENDING"]

    wallet = snap["wallet_cash"]
    paper_pnl = snap["paper_pnl"]
    bankroll = snap["bankroll"]
    open_stake = snap["open_stakes"]
    mode = trading_mode()
    closed_pnl = round(sum(float(r.get("pnl") or 0) for r in closed), 2)

    account_pnl = None
    account_pnl_pct = None
    if mode == "live":
        from bot_common import account_pnl_snapshot

        acct = account_pnl_snapshot(live_wallet=live_wallet)
        session_start = float(acct.get("starting_wallet_usdc") or 0)
        start = session_start if session_start > 0 else wallet
        account_pnl = acct.get("account_pnl")
        account_pnl_pct = acct.get("account_pnl_pct")
        equity = round(wallet, 2)
        total_pnl = closed_pnl
        realized_pnl = closed_pnl
    else:
        start = round(wallet + paper_pnl - closed_pnl, 2) if closed else wallet
        if not closed:
            start = wallet
        equity = round(wallet + paper_pnl, 2)
        total_pnl = round(paper_pnl, 2)
        realized_pnl = paper_pnl

    ev = round(realized_pnl / len(closed), 2) if closed else 0.0

    session_hkt = None
    if mode == "live":
        session_hkt = load_live_session().get("started_at_hkt")
    curve, curve_labels = build_equity_curve_with_labels(
        start,
        closed,
        session_start_hkt=session_hkt,
        pad_to=equity if mode != "live" and not closed else None,
    )

    old_curve: list[float] = []
    old_run = 0.0
    old_closed: list[dict] = []
    if not is_package2_active():
        old_curve = [start if (closed or mode == "live") else wallet]
        old_run = old_curve[0]
        for r in sorted(paper_rows, key=lambda x: x.get("timestamp_hkt", "")):
            sim = r.get("old_strategy_sim") or {}
            resolved = (r.get("resolved_outcome") or "").upper()
            if sim.get("would_enter") and resolved in ("UP", "DOWN"):
                old_closed.append(r)
                old_run = round(old_run + float(sim.get("pnl") or 0), 2)
                old_curve.append(old_run)

    if is_package2_active():
        tiers = package2_tiers_preview()
        dynamic = False
    else:
        tiers = dynamic_tiers_preview(bankroll)
        dynamic = True
    ws = read_window_stats()
    p2_cfg = package2_tiers_preview()
    daily_pnl = daily_realized_pnl_hkt(rows) if is_package2_active() else None

    return {
        "strategy": strat,
        "dynamic_bankroll": dynamic,
        "trading_mode": mode,
        "wallet_cash": wallet,
        "starting_bankroll": start,
        "current_bankroll": bankroll,
        "open_stakes": open_stake,
        "equity": equity,
        "total_pnl": total_pnl,
        "overall_pnl": total_pnl,
        "trade_log_pnl": total_pnl,
        "account_pnl": account_pnl,
        "account_pnl_pct": account_pnl_pct,
        "starting_wallet_usdc": start if mode == "live" else None,
        "daily_pnl_hkt": daily_pnl if daily_pnl is not None else 0.0,
        "total_pnl_pct": round(total_pnl / start * 100, 2) if start else 0,
        "trade_log_pnl_pct": round(total_pnl / start * 100, 2) if start else 0,
        "trades_entered": len(entered),
        "trades_attempted": len(entered) + len(no_fill),
        "trades_no_fill": len(no_fill),
        "trades_failed": len(failed),
        "trades_skipped": len(skipped),
        "trades_total": len(rows),
        "trades_open": len(open_rows),
        "trades_closed": len(closed),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "ev_per_trade": ev,
        "max_drawdown": _max_drawdown(curve),
        "equity_curve": curve,
        "equity_curve_labels": curve_labels,
        "dynamic_tiers": tiers,
        "observation": {
            "windows_judged": len(rows),
            "windows_entered": len(entered),
            "windows_skipped": len(skipped),
            "windows_failed": len(failed),
            "windows_open": len(open_rows),
            "windows_closed": len(closed),
        },
        "window_stats": ws,
        "package2": {
            "daily_pnl_hkt": daily_pnl,
            "daily_loss_limit": p2_cfg.get("stakes") and package2_config().get("daily_loss_limit_usd"),
            "global_paused": global_paused() if is_package2_active() else False,
            "favorite_paused": favorite_paused() if is_package2_active() else False,
            "underdog_paused": underdog_paused() if is_package2_active() else False,
            "risk_pause": risk_pause_status(asset="btc") if is_package2_active() else None,
        } if is_package2_active() else None,
        "old_strategy": None if is_package2_active() else {
            "total_pnl": round(old_run - old_curve[0], 2) if len(old_curve) > 1 else 0,
            "equity_curve": old_curve,
            "trades": sum(
                1 for r in paper_rows if (r.get("old_strategy_sim") or {}).get("would_enter")
            ),
            "trades_settled": len(old_closed),
            "trades_pending": sum(
                1 for r in paper_rows
                if (r.get("old_strategy_sim") or {}).get("would_enter")
                and (r.get("resolved_outcome") or "") in ("PENDING", "NONE", "", None)
            ),
            "wins": sum(
                1 for r in old_closed
                if float((r.get("old_strategy_sim") or {}).get("pnl") or 0) > 0
            ),
            "losses": sum(
                1 for r in old_closed
                if float((r.get("old_strategy_sim") or {}).get("pnl") or 0) < 0
            ),
        },
        "live_session": load_live_session(),
    }


def paper_summary() -> dict[str, Any]:
    s = new_strategy_summary()
    return {
        "mode": "paper",
        "strategy": "NEW",
        "initial_capital": s["starting_bankroll"],
        "cash": s["current_bankroll"],
        "equity": s["equity"],
        "total_pnl": s["total_pnl"],
        "return_pct": s["total_pnl_pct"],
        "trades_total": s["trades_entered"] + s["trades_skipped"],
        "trades_settled": s["trades_closed"],
        "trades_open": s["trades_open"],
        "trades_skipped": s["trades_skipped"],
        "wins": s["wins"],
        "losses": s["losses"],
        "win_rate": s["win_rate"],
        "ev_per_trade": s["ev_per_trade"],
        "max_drawdown": s["max_drawdown"],
        "bet_size": s.get("dynamic_tiers", {}).get("stakes", {}).get("1x", 0),
        "daily_limit_unlimited": True,
    }


def load_trades() -> list[dict]:
    return [r for r in load_trade_log() if r.get("entered")]


def old_strategy_trade_log() -> list[dict]:
    """Simulated OLD strategy rows (ask ≤ 0.88 · fixed $5) for dashboard Trade Log."""
    rows: list[dict] = []
    for r in load_paper_log():
        sim = r.get("old_strategy_sim") or {}
        if not sim.get("would_enter"):
            continue
        res = (r.get("resolved_outcome") or "").upper()
        side = r.get("side") or "up"
        ask = r.get("entry_ask")
        if ask is None and isinstance(r.get("checkpoint"), dict):
            ask = r["checkpoint"].get("ask")
        contract = side_to_contract(side)
        if res in ("UP", "DOWN"):
            won = res == contract
            result = "WIN" if won else "LOSS"
            pnl = float(sim.get("pnl") or 0)
            if ask:
                pnl = calc_win_pnl(OLD_BET, float(ask)) if won else calc_loss_pnl(OLD_BET)
        else:
            result = "PENDING"
            pnl = 0.0
        rows.append({
            "timestamp_hkt": r.get("timestamp_hkt"),
            "market": r.get("market"),
            "slug": r.get("slug"),
            "bought_label": bought_label(contract, ask, True),
            "entry_ask": round(float(ask), 4) if ask is not None else None,
            "stake": OLD_BET,
            "multiplier": "1x",
            "resolved_label": resolved_label(res, contract),
            "trade_result": result,
            "pnl": round(pnl, 2),
            "strategy_reason": "OLD sim · ask ≤ 0.88 · fixed $5",
        })
    return rows[::-1]
