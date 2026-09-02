#!/usr/bin/env python3
"""Phase 1: observe BTC 5m windows — paper trade when strategy rules pass."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path("/root/btc-5m-bot")
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from bot_common import (  # noqa: E402
    OBSERVATIONS_FILE,
    STATE_FILE,
    WINDOWS_FILE,
    log_error,
    setup_logger,
    write_window_stats,
)
from polymarket_btc import (  # noqa: E402
    discover_current_market,
    fetch_market_outcome,
    fetch_order_book,
    seconds_to_end,
    summarize_book,
    utc_iso,
    window_start_ts,
)
from price_feeds import fetch_btc_usd  # noqa: E402
from strategy_rules import evaluate_snapshot, best_entry_checkpoint  # noqa: E402

TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()
from paper_engine import (  # noqa: E402
    evaluate_window_counterfactual,
    log_window_trade_decision,
    log_session_pnl,
    settle_from_polymarket,
    settle_stale_open_trades,
    try_live_entry_if_ready,
    backfill_live_log_from_paper,
    backfill_window_trade_decisions,
    dedupe_session_trade_logs,
    _slug_logged,
)
from package2_strategy import is_package2_active, trading_session_info, trading_session_skip_reason  # noqa: E402

CHECKPOINTS = [75, 60, 50, 40, 30, 25, 12, 5]
TRADE_ACTIVE = TRADING_MODE in ("paper", "live")
POLL_SEC = float(os.getenv("OBSERVER_POLL_SEC", "5"))
OBSERVE_T_START = int(os.getenv("OBSERVE_T_START", "75"))


def _load_windows() -> list[dict]:
    if not WINDOWS_FILE.exists():
        return []
    with WINDOWS_FILE.open() as f:
        return json.load(f)


def _save_windows(windows: list[dict]) -> None:
    trimmed = windows[-500:]
    WINDOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with WINDOWS_FILE.open("w") as f:
        json.dump(trimmed, f, indent=2)
    write_window_stats(trimmed)


def _append_observation(row: dict) -> None:
    OBSERVATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OBSERVATIONS_FILE.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    with STATE_FILE.open() as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)


def _pick_new_snap(checkpoints: list[dict]) -> dict | None:
    best = best_entry_checkpoint(checkpoints)
    if best:
        return best
    in_window = [c for c in checkpoints if c.get("in_entry_window")]
    if not in_window:
        return None
    with_ask = [c for c in in_window if c.get("ask") is not None]
    if not with_ask:
        return in_window[0]
    return min(with_ask, key=lambda c: c.get("ask") or 999)


def _enrich_snapshot(snap: dict) -> dict:
    """Attach trading-session context for 24/7 logs and backtest."""
    info = trading_session_info(asset="btc")
    snap["trading_session"] = info
    snap["in_trading_session"] = info["in_trading_session"]
    snap["trading_allowed"] = info["trading_allowed"]
    if not info["trading_allowed"]:
        snap["session_skip_reason"] = trading_session_skip_reason(asset="btc")
    return snap


def _finalize_window(record: dict) -> None:
    checkpoints = record.get("checkpoints", [])
    best = best_entry_checkpoint(checkpoints)
    record["best_entry"] = best
    record["had_tradable_entry"] = best is not None
    record["finished_at"] = utc_iso()
    record["trading_session_at_end"] = trading_session_info(asset="btc")

    if best and is_package2_active():
        record["backtest_counterfactual"] = evaluate_window_counterfactual(best)
    elif best:
        record["backtest_counterfactual"] = {
            "would_enter": bool(best.get("would_enter")),
            "checkpoint": best.get("checkpoint"),
            "ask": best.get("ask"),
            "side": best.get("side"),
        }

    pm = fetch_market_outcome(record["slug"])
    if pm:
        record["outcome"] = pm["outcome"]
        record["outcome_source"] = "polymarket"
        record["polymarket_prices"] = pm.get("outcome_prices")
        if best:
            record["hypo_win"] = best.get("side") == record["outcome"]
    else:
        record["outcome"] = None
        record["outcome_pending"] = True
        open_px = record.get("open_btc_price")
        if open_px:
            btc = fetch_btc_usd()
            record["close_btc_price"] = btc["price"]
            record["close_btc_source"] = btc["source"]
            record["outcome_estimate"] = "up" if btc["price"] >= open_px else "down"
            record["delta_usd_final"] = round(btc["price"] - open_px, 2)

    if TRADING_MODE in ("paper", "live"):
        trade_row = log_window_trade_decision(
            record["slug"],
            record.get("title", ""),
            checkpoints,
            record.get("open_btc_price") or 0.0,
        )
        if trade_row:
            record["trade_decision"] = {
                "entered": trade_row.get("entered"),
                "trade_result": trade_row.get("trade_result"),
                "strategy_reason": trade_row.get("strategy_reason"),
                "skip_category": trade_row.get("skip_category"),
                "trading_session": trade_row.get("trading_session"),
                "tier": trade_row.get("tier"),
                "stake": trade_row.get("stake"),
            }
        settle_from_polymarket(record["slug"])

    try:
        from alpha_max import log_alpha_window

        alpha_row = log_alpha_window(
            asset="BTC",
            slug=record["slug"],
            title=record.get("title", ""),
            checkpoints=checkpoints,
            outcome=record.get("outcome") or record.get("outcome_estimate"),
        )
        if alpha_row:
            record["alpha_max_trade"] = {
                "entered": True,
                "trade_result": alpha_row.get("trade_result"),
                "pnl": alpha_row.get("pnl"),
                "strategy_reason": alpha_row.get("strategy_reason"),
                "tier": alpha_row.get("tier"),
                "stake": alpha_row.get("stake"),
            }
    except Exception as exc:
        setup_logger().warning("Alpha-Max BTC paper failed %s: %s", record.get("slug"), exc)

    windows = _load_windows()
    windows.append(record)
    _save_windows(windows)
    setup_logger().info(
        "Window %s done: tradable=%s outcome=%s source=%s",
        record.get("slug"),
        record.get("had_tradable_entry"),
        record.get("outcome") or record.get("outcome_estimate", "pending"),
        record.get("outcome_source", "pending"),
    )


def _take_snapshot(
    market,
    t_rem: float,
    open_price: float | None,
    checkpoint_label: str | None = None,
) -> dict:
    btc = fetch_btc_usd()
    price = btc["price"]
    delta_usd = (price - open_price) if open_price else 0.0
    delta_pct = (delta_usd / open_price * 100) if open_price else 0.0

    up_raw = fetch_order_book(market.up_token_id)
    down_raw = fetch_order_book(market.down_token_id)
    up_book = summarize_book(up_raw)
    down_book = summarize_book(down_raw)

    eval_result = evaluate_snapshot(t_rem, delta_usd, delta_pct, up_book, down_book)

    snap = _enrich_snapshot({
        "ts": utc_iso(),
        "checkpoint": checkpoint_label,
        "t_remaining": round(t_rem, 1),
        "btc_price": price,
        "btc_source": btc["source"],
        "open_btc_price": open_price,
        "delta_usd": round(delta_usd, 2),
        "delta_pct": round(delta_pct, 4),
        "up_book": up_book,
        "down_book": down_book,
        **eval_result,
    })
    _append_observation({"slug": market.slug, **snap})
    return snap


def run_once() -> None:
    """Single poll cycle — useful for testing."""
    market = discover_current_market()
    if not market:
        print("No active market found")
        return
    t_rem = seconds_to_end(market)
    btc = fetch_btc_usd()
    snap = _take_snapshot(market, t_rem, btc["price"])
    print(json.dumps({"slug": market.slug, "title": market.title, **snap}, indent=2))


def run_loop() -> None:
    logger = setup_logger()
    mode_label = "LIVE TRADE" if TRADING_MODE == "live" else ("PAPER TRADE" if TRADE_ACTIVE else "LOG ONLY")
    logger.info("BTC 5m observer started (%s)", mode_label)
    sess = trading_session_info(asset="btc")
    logger.info(
        "Trading window: %s · %s · in_us_session=%s · btc_extended=%s · btc_dog_only=%s · trading_allowed=%s",
        sess["trading_hours_et"],
        sess["trading_hours_hkt"],
        sess["in_us_regular_session"],
        sess["extended_hours_live"],
        sess.get("extended_dog_only"),
        sess["trading_allowed"],
    )
    if TRADE_ACTIVE:
        n = settle_stale_open_trades()
        if n:
            logger.info("Settled %s stale open paper trade(s) on startup", n)
        if TRADING_MODE == "live":
            live_rm, paper_rm = dedupe_session_trade_logs()
            if live_rm or paper_rm:
                logger.info("Deduped trade logs: %s live, %s paper duplicate row(s)", live_rm, paper_rm)
            bf = backfill_live_log_from_paper()
            if bf:
                logger.info("Backfilled %s live log row(s) from paper session", bf)
            wf = backfill_window_trade_decisions()
            if wf:
                logger.info("Backfilled %s window trade decision row(s) from windows.json", wf)
        log_session_pnl("startup")
        from package2_strategy import v1_stake_startup_banner

        for line in v1_stake_startup_banner():
            logger.info(line)

    state = _load_state()
    current_slug: str | None = state.get("current_slug")
    open_price: float | None = state.get("open_btc_price")
    window_record: dict | None = None
    seen_checkpoints: set[int] = set()
    wallet_check_counter = 0

    if state.get("window_record"):
        window_record = state["window_record"]
        seen_checkpoints = set(state.get("seen_checkpoints", []))

    while True:
        try:
            market = discover_current_market()
            if not market:
                time.sleep(POLL_SEC)
                continue

            t_rem = seconds_to_end(market)

            # New window
            if market.slug != current_slug:
                if window_record and current_slug:
                    _finalize_window(window_record)
                    if TRADE_ACTIVE:
                        settle_stale_open_trades()

                btc = fetch_btc_usd()
                open_price = btc["price"]
                current_slug = market.slug
                seen_checkpoints = set()
                window_record = {
                    "slug": market.slug,
                    "title": market.title,
                    "window_start_ts": market.window_start_ts,
                    "window_end_ts": market.window_end_ts,
                    "started_at": utc_iso(),
                    "open_btc_price": open_price,
                    "open_btc_source": btc["source"],
                    "trading_session_at_start": trading_session_info(asset="btc"),
                    "checkpoints": [],
                }
                logger.info(
                    "Tracking window %s (%s) · in_session=%s · %s",
                    market.slug,
                    market.title,
                    window_record["trading_session_at_start"]["in_trading_session"],
                    window_record["trading_session_at_start"]["trading_hours_note"],
                )

            if window_record is None:
                time.sleep(POLL_SEC)
                continue

            # Checkpoint snapshots
            for cp in CHECKPOINTS:
                if cp in seen_checkpoints:
                    continue
                if t_rem <= cp + 2 and t_rem >= cp - 3:
                    label = f"T-{cp}"
                    snap = _take_snapshot(market, t_rem, open_price, label)
                    window_record["checkpoints"].append(snap)
                    seen_checkpoints.add(cp)
                    if TRADING_MODE == "live" and TRADE_ACTIVE:
                        live_row = try_live_entry_if_ready(
                            market.slug,
                            market.title,
                            snap,
                            open_price or 0.0,
                        )
                        if live_row:
                            window_record["live_trade"] = live_row
                            logger.info(
                                "Live entry attempt %s result=%s filled=%s",
                                market.slug,
                                live_row.get("trade_result"),
                                live_row.get("filled"),
                            )
                    _save_state({
                        "current_slug": current_slug,
                        "open_btc_price": open_price,
                        "window_record": window_record,
                        "seen_checkpoints": list(seen_checkpoints),
                    })

            # Continuous observation during T-80..T-0 (light polling logged only at checkpoints)
            if t_rem <= 0 and window_record:
                _finalize_window(window_record)
                if TRADE_ACTIVE:
                    settle_stale_open_trades()
                try:
                    from manual_bets_sync import sync_manual_bets_if_due

                    sync_manual_bets_if_due(logger=logger, reason="btc_window_end")
                except Exception:
                    pass
                window_record = None
                current_slug = None
                open_price = None
                seen_checkpoints = set()
                _save_state({})

            wallet_check_counter += 1
            if wallet_check_counter >= 12:
                wallet_check_counter = 0
                try:
                    from capital_launch import maybe_launch_on_capital
                    from new_strategy import fetch_wallet_cash

                    launch = maybe_launch_on_capital(fetch_wallet_cash(live=True), logger=logger)
                    if launch.get("launched"):
                        logger.info("Capital launch triggered from observer: %s", launch.get("btc_session_id"))
                except Exception as exc:
                    log_error("Capital launch check failed", exc)
                try:
                    from backtest_research import refresh_live_session_research, sync_backtest_tape_from_logs

                    sync_backtest_tape_from_logs()
                    refresh_live_session_research()
                except Exception:
                    pass

            time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            logger.info("Observer stopped by user")
            break
        except Exception as exc:
            log_error("Observer loop error", exc)
            time.sleep(POLL_SEC * 2)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    else:
        run_loop()
