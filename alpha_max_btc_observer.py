#!/usr/bin/env python3
"""Alpha-Max live BTC 5m observer — Bot #2 instance."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path("/root/btc-5m-bot")
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_INSTANCE", "bot2")

from bot_instance import bootstrap_instance, log_root  # noqa: E402

bootstrap_instance(default_instance="bot2")

from bot_common import log_error, setup_logger, write_window_stats  # noqa: E402
from alpha_max_live import (  # noqa: E402
    ensure_live_session,
    finalize_alpha_observer_window,
    settle_stale_alpha_open_trades,
    try_alpha_live_entry_if_ready,
)
from polymarket_crypto import (  # noqa: E402
    discover_current_market,
    fetch_order_book,
    seconds_to_end,
    summarize_book,
)
from polymarket_btc import utc_iso  # noqa: E402
from price_feeds import fetch_btc_usd  # noqa: E402
from strategy_rules import evaluate_snapshot  # noqa: E402

LOG = log_root()
WINDOWS_FILE = LOG / "alpha_max_btc_windows.json"
STATE_FILE = LOG / "alpha_max_btc_observer_state.json"
OBSERVATIONS_FILE = LOG / "alpha_max_btc_observations.jsonl"
CHECKPOINTS = [75, 60, 50, 40, 30, 25, 12, 5]
POLL_SEC = float(os.getenv("OBSERVER_POLL_SEC", "5"))


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text())


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _append_obs(row: dict) -> None:
    OBSERVATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OBSERVATIONS_FILE.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _take_snapshot(market, t_rem: float, open_price: float | None, checkpoint: str | None) -> dict:
    btc = fetch_btc_usd()
    price = btc["price"]
    delta_usd = (price - open_price) if open_price else 0.0
    delta_pct = (delta_usd / open_price * 100) if open_price else 0.0
    up_raw = fetch_order_book(market.up_token_id)
    down_raw = fetch_order_book(market.down_token_id)
    up_book = summarize_book(up_raw)
    down_book = summarize_book(down_raw)
    ev = evaluate_snapshot(t_rem, delta_usd, delta_pct, up_book, down_book)
    snap = {
        "ts": utc_iso(),
        "checkpoint": checkpoint,
        "t_remaining": round(t_rem, 1),
        "btc_price": price,
        "open_btc_price": open_price,
        "delta_usd": round(delta_usd, 2),
        "delta_pct": round(delta_pct, 4),
        "up_book": up_book,
        "down_book": down_book,
        **ev,
    }
    _append_obs({"slug": market.slug, **snap})
    return snap


def _finalize_window(record: dict) -> None:
    record = finalize_alpha_observer_window(record)
    windows = []
    if WINDOWS_FILE.exists():
        try:
            windows = json.loads(WINDOWS_FILE.read_text())
        except json.JSONDecodeError:
            windows = []
    windows.append(record)
    windows = windows[-500:]
    WINDOWS_FILE.write_text(json.dumps(windows, indent=2))
    write_window_stats(windows)


def run_loop() -> None:
    logger = setup_logger()
    ensure_live_session()
    logger.info(
        "Alpha-Max BTC observer started (instance=%s mode=%s)",
        os.getenv("BOT_INSTANCE"),
        os.getenv("TRADING_MODE", "paper"),
    )
    if os.getenv("TRADING_MODE", "paper") == "live":
        n = settle_stale_alpha_open_trades()
        if n:
            logger.info("Settled %s stale Alpha live trade(s) on startup", n)

    state = _load_state()
    current_slug = state.get("current_slug")
    open_price = state.get("open_btc_price")
    window_record = state.get("window_record")
    seen = set(state.get("seen_checkpoints", []))

    while True:
        try:
            market = discover_current_market("btc")
            if not market:
                time.sleep(POLL_SEC)
                continue
            t_rem = seconds_to_end(market)
            if market.slug != current_slug:
                if window_record and current_slug:
                    _finalize_window(window_record)
                    if os.getenv("TRADING_MODE", "paper") == "live":
                        settle_stale_alpha_open_trades()
                btc = fetch_btc_usd()
                open_price = btc["price"]
                current_slug = market.slug
                seen = set()
                window_record = {
                    "asset": "BTC",
                    "slug": market.slug,
                    "title": market.title,
                    "open_btc_price": open_price,
                    "started_at": utc_iso(),
                    "checkpoints": [],
                }
                _save_state({
                    "current_slug": current_slug,
                    "open_btc_price": open_price,
                    "window_record": window_record,
                    "seen_checkpoints": [],
                })

            if not window_record:
                time.sleep(POLL_SEC)
                continue

            for cp in CHECKPOINTS:
                if cp in seen:
                    continue
                if t_rem <= cp + 2 and t_rem >= cp - 3:
                    snap = _take_snapshot(market, t_rem, open_price, f"T-{cp}")
                    window_record["checkpoints"].append(snap)
                    seen.add(cp)
                    live_row = try_alpha_live_entry_if_ready(
                        asset="BTC",
                        slug=market.slug,
                        title=market.title,
                        snapshot=snap,
                        checkpoints=list(window_record.get("checkpoints") or []),
                    )
                    if live_row:
                        window_record["live_trade"] = live_row
                        logger.info(
                            "Alpha-Max BTC live %s filled=%s result=%s stake=$%.2f",
                            market.slug,
                            live_row.get("filled"),
                            live_row.get("trade_result"),
                            float(live_row.get("stake") or 0),
                        )
                    _save_state({
                        "current_slug": current_slug,
                        "open_btc_price": open_price,
                        "window_record": window_record,
                        "seen_checkpoints": list(seen),
                    })

            if t_rem <= 0:
                _finalize_window(window_record)
                if os.getenv("TRADING_MODE", "paper") == "live":
                    settle_stale_alpha_open_trades()
                current_slug = None
                window_record = None
                open_price = None
                seen = set()
                _save_state({})
            time.sleep(POLL_SEC)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            log_error("Alpha-Max BTC observer error", exc)
            time.sleep(POLL_SEC * 2)


if __name__ == "__main__":
    run_loop()
