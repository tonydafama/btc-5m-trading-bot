#!/usr/bin/env python3
"""ETH 5m observer — Package 2+ paper or live (ETH_TRADING_MODE)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path("/root/btc-5m-bot")
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

ETH_TRADING_MODE = os.getenv("ETH_TRADING_MODE", "paper").lower()

from bot_common import (
    ETH_OBSERVATIONS_FILE,
    ETH_STATE_FILE,
    ETH_WINDOWS_FILE,
    log_error,
    setup_logger,
    write_eth_window_stats,
)
from eth_paper_engine import (
    backfill_eth_window_decisions,
    evaluate_eth_window_counterfactual,
    eth_trading_mode,
    log_eth_session_pnl,
    log_eth_window_decision,
    settle_eth_from_polymarket,
    settle_stale_eth_trades,
    try_eth_live_entry_if_ready,
)
from package2_strategy import is_package2_active, trading_session_info, trading_session_skip_reason
from polymarket_btc import utc_iso
from polymarket_crypto import (
    discover_current_market,
    fetch_market_outcome,
    fetch_order_book,
    seconds_to_end,
    summarize_book,
)
from price_feeds import fetch_eth_usd
from eth_strategy_rules import best_entry_checkpoint, evaluate_eth_snapshot

CHECKPOINTS = [75, 60, 50, 40, 30, 25, 12, 5]
POLL_SEC = float(__import__("os").getenv("ETH_OBSERVER_POLL_SEC", "5"))
OBSERVE_T_START = int(__import__("os").getenv("OBSERVE_T_START", "75"))


def _load_windows() -> list[dict]:
    if not ETH_WINDOWS_FILE.exists():
        return []
    with ETH_WINDOWS_FILE.open() as f:
        return json.load(f)


def _save_windows(windows: list[dict]) -> None:
    trimmed = windows[-500:]
    ETH_WINDOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ETH_WINDOWS_FILE.open("w") as f:
        json.dump(trimmed, f, indent=2)
    write_eth_window_stats(trimmed)


def _append_observation(row: dict) -> None:
    ETH_OBSERVATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ETH_OBSERVATIONS_FILE.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _load_state() -> dict:
    if not ETH_STATE_FILE.exists():
        return {}
    with ETH_STATE_FILE.open() as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    ETH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ETH_STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)


def _enrich_snapshot(snap: dict) -> dict:
    info = trading_session_info(asset="eth")
    snap["trading_session"] = info
    snap["in_trading_session"] = info["in_trading_session"]
    snap["trading_allowed"] = info["trading_allowed"]
    if not info["trading_allowed"]:
        snap["session_skip_reason"] = trading_session_skip_reason(asset="eth")
    return snap


def _take_snapshot(market, t_rem: float, open_price: float | None, checkpoint_label: str | None = None) -> dict:
    eth = fetch_eth_usd()
    price = eth["price"]
    delta_usd = (price - open_price) if open_price else 0.0
    delta_pct = (delta_usd / open_price * 100) if open_price else 0.0

    up_raw = fetch_order_book(market.up_token_id)
    down_raw = fetch_order_book(market.down_token_id)
    up_book = summarize_book(up_raw)
    down_book = summarize_book(down_raw)

    eval_result = evaluate_eth_snapshot(t_rem, delta_usd, delta_pct, up_book, down_book)

    snap = _enrich_snapshot({
        "ts": utc_iso(),
        "checkpoint": checkpoint_label,
        "t_remaining": round(t_rem, 1),
        "eth_price": price,
        "eth_source": eth["source"],
        "open_eth_price": open_price,
        "delta_usd": round(delta_usd, 2),
        "delta_pct": round(delta_pct, 4),
        "up_book": up_book,
        "down_book": down_book,
        **eval_result,
    })
    _append_observation({"slug": market.slug, "asset": "ETH", **snap})
    return snap


def _finalize_window(record: dict) -> None:
    checkpoints = record.get("checkpoints", [])
    best = best_entry_checkpoint(checkpoints)
    record["best_entry"] = best
    record["had_tradable_entry"] = best is not None
    record["finished_at"] = utc_iso()
    record["trading_session_at_end"] = trading_session_info(asset="eth")

    if best and is_package2_active():
        record["backtest_counterfactual"] = evaluate_eth_window_counterfactual(best)
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
        if best:
            record["hypo_win"] = best.get("side") == record["outcome"]
    else:
        record["outcome"] = None
        record["outcome_pending"] = True

    trade_row = log_eth_window_decision(
        record["slug"],
        record.get("title", ""),
        checkpoints,
        record.get("open_eth_price") or 0.0,
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
        record["paper_trade"] = trade_row
        if eth_trading_mode() == "live":
            record["live_trade"] = trade_row
    settle_eth_from_polymarket(record["slug"])

    try:
        from alpha_max import log_alpha_window

        alpha_row = log_alpha_window(
            asset="ETH",
            slug=record["slug"],
            title=record.get("title", ""),
            checkpoints=checkpoints,
            outcome=record.get("outcome"),
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
        setup_logger().warning("Alpha-Max ETH paper failed %s: %s", record.get("slug"), exc)

    windows = _load_windows()
    windows.append(record)
    _save_windows(windows)
    setup_logger().info(
        "ETH window %s done: tradable=%s outcome=%s decision=%s",
        record.get("slug"),
        record.get("had_tradable_entry"),
        record.get("outcome") or "pending",
        (record.get("trade_decision") or {}).get("strategy_reason", "")[:60],
    )


def run_loop() -> None:
    logger = setup_logger()
    mode_label = "LIVE TRADE" if eth_trading_mode() == "live" else "PAPER"
    logger.info(
        "ETH 5m observer started (%s · Package 2+ · variant A eth_move≥$1.0 when live)",
        mode_label,
    )
    sess = trading_session_info(asset="eth")
    logger.info(
        "Trading window: %s · %s · in_us_session=%s · eth_extended=%s · trading_allowed=%s",
        sess["trading_hours_et"],
        sess["trading_hours_hkt"],
        sess["in_us_regular_session"],
        sess["extended_hours_live"],
        sess["trading_allowed"],
    )

    n = settle_stale_eth_trades()
    if n:
        logger.info("Settled %s stale ETH paper trade(s) on startup", n)
    bf = backfill_eth_window_decisions()
    if bf:
        logger.info("Backfilled %s ETH window trade decision row(s)", bf)
    log_eth_session_pnl("startup")
    from package2_strategy import v1_stake_startup_banner

    for line in v1_stake_startup_banner():
        logger.info(line)

    state = _load_state()
    current_slug: str | None = state.get("current_slug")
    open_price: float | None = state.get("open_eth_price")
    window_record: dict | None = state.get("window_record")
    seen_checkpoints: set[int] = set(state.get("seen_checkpoints", []))
    wallet_check_counter = 0

    while True:
        try:
            market = discover_current_market("eth")
            if not market:
                time.sleep(POLL_SEC)
                continue

            slug = market.slug
            t_rem = seconds_to_end(market)

            if slug != current_slug:
                if window_record and current_slug:
                    _finalize_window(window_record)
                    settled = settle_stale_eth_trades()
                    if settled:
                        logger.info("Settled %s stale ETH paper trade(s)", settled)
                current_slug = slug
                eth = fetch_eth_usd()
                open_price = eth["price"]
                window_record = {
                    "asset": "ETH",
                    "slug": slug,
                    "title": market.title,
                    "window_start_ts": market.window_start_ts,
                    "open_eth_price": open_price,
                    "open_eth_source": eth["source"],
                    "started_at": utc_iso(),
                    "checkpoints": [],
                }
                seen_checkpoints = set()
                _save_state(
                    {
                        "current_slug": current_slug,
                        "open_eth_price": open_price,
                        "window_record": window_record,
                        "seen_checkpoints": [],
                    }
                )

            if window_record is None:
                time.sleep(POLL_SEC)
                continue

            for cp in CHECKPOINTS:
                if cp in seen_checkpoints:
                    continue
                if t_rem <= cp + 2 and t_rem >= cp - 3:
                    snap = _take_snapshot(market, t_rem, open_price, f"T-{cp}")
                    window_record["checkpoints"].append(snap)
                    seen_checkpoints.add(cp)
                    if eth_trading_mode() == "live":
                        live_row = try_eth_live_entry_if_ready(
                            market.slug,
                            market.title,
                            snap,
                            open_price or 0.0,
                        )
                        if live_row:
                            window_record["live_trade"] = live_row
                            logger.info(
                                "ETH live entry %s result=%s filled=%s",
                                market.slug,
                                live_row.get("trade_result"),
                                live_row.get("filled"),
                            )
                    _save_state(
                        {
                            "current_slug": current_slug,
                            "open_eth_price": open_price,
                            "window_record": window_record,
                            "seen_checkpoints": list(seen_checkpoints),
                        }
                    )

            if t_rem <= 0:
                _finalize_window(window_record)
                try:
                    from manual_bets_sync import sync_manual_bets_if_due

                    sync_manual_bets_if_due(logger=logger, reason="eth_window_end")
                except Exception:
                    pass
                current_slug = None
                window_record = None
                open_price = None
                seen_checkpoints = set()
                _save_state({})

            else:
                _save_state(
                    {
                        "current_slug": current_slug,
                        "open_eth_price": open_price,
                        "window_record": window_record,
                        "seen_checkpoints": list(seen_checkpoints),
                    }
                )

            wallet_check_counter += 1
            if wallet_check_counter >= 12:
                wallet_check_counter = 0
                try:
                    from capital_launch import maybe_launch_on_capital
                    from new_strategy import fetch_wallet_cash

                    launch = maybe_launch_on_capital(fetch_wallet_cash(live=True), logger=logger)
                    if launch.get("launched"):
                        logger.info("Capital launch triggered from ETH observer: %s", launch.get("eth_session_id"))
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
            logger.info("ETH observer stopped")
            break
        except Exception as exc:
            log_error("ETH observer loop error", exc)
            time.sleep(POLL_SEC * 2)


if __name__ == "__main__":
    run_loop()
