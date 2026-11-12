#!/usr/bin/env python3
"""Daemon: 先有波幅，先買中間價 (single-leg BTC 5m)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "config" / "midrange_vol_live.env")

from bot_common import setup_logger  # noqa: E402
from midrange_vol_engine import (  # noqa: E402
    append_event,
    ensure_session,
    settle_open_trades,
    state_path,
    try_entry,
)
from midrange_vol_strategy import (  # noqa: E402
    evaluate_entry,
    note_first_to_band,
    rules_summary,
)
from polymarket_btc import (  # noqa: E402
    discover_current_market,
    fetch_order_book,
    seconds_to_end,
    summarize_book,
)
from price_feeds import prior_binance_5m_range_usd  # noqa: E402

POLL_SEC = float(os.getenv("MIDVOL_POLL_SEC", os.getenv("OBSERVER_POLL_SEC", "5")))


def _load_state() -> dict:
    if not state_path().is_file():
        return {}
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(state: dict) -> None:
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def run_loop() -> None:
    logger = setup_logger()
    ensure_session()
    settle_open_trades()
    append_event({"event": "daemon_start", "rules": rules_summary()})
    logger.info("Midrange-vol daemon started · %s", rules_summary().get("label"))

    state = _load_state()
    slug = state.get("slug")
    track = state.get("track") or {}
    prior_range = state.get("prior_range_usd")
    entered = bool(state.get("entered"))

    while True:
        try:
            market = discover_current_market()
            if not market:
                time.sleep(POLL_SEC)
                continue
            t_rem = seconds_to_end(market)
            if market.slug != slug:
                settle_open_trades()
                slug = market.slug
                prior = prior_binance_5m_range_usd(market.window_start_ts)
                prior_range = prior.get("range_usd")
                track = {}
                entered = False
                append_event(
                    {
                        "event": "window_open",
                        "slug": slug,
                        "prior_range_usd": prior_range,
                    }
                )

            up_book = summarize_book(fetch_order_book(market.up_token_id))
            down_book = summarize_book(fetch_order_book(market.down_token_id))
            up_ask = up_book.get("best_ask")
            down_ask = down_book.get("best_ask")

            if t_rem <= 80:
                note_first_to_band(
                    track,
                    up_ask=up_ask,
                    down_ask=down_ask,
                    t_remaining=t_rem,
                )

            if not entered:
                decision, reason = evaluate_entry(
                    t_remaining=t_rem,
                    prior_range_usd=prior_range if prior_range is not None else None,
                    up_ask=up_ask,
                    down_ask=down_ask,
                    track=track,
                )
                if decision:
                    row = try_entry(market=market, decision=decision, t_remaining=t_rem)
                    if row:
                        entered = True
                elif (
                    40 <= t_rem <= 75
                    and reason not in ("outside_entry_window",)
                    and state.get("last_skip_key") != f"{slug}:{reason}"
                ):
                    append_event(
                        {
                            "event": "skip",
                            "slug": slug,
                            "reason": reason,
                            "t_remaining": round(t_rem, 1),
                            "up_ask": up_ask,
                            "down_ask": down_ask,
                            "prior_range_usd": prior_range,
                            "first_side": track.get("first_side"),
                        }
                    )
                    state["last_skip_key"] = f"{slug}:{reason}"

            _save_state(
                {
                    "slug": slug,
                    "track": track,
                    "prior_range_usd": prior_range,
                    "entered": entered,
                    "last_skip_key": state.get("last_skip_key"),
                }
            )
            if t_rem < 2:
                entered = False
            time.sleep(POLL_SEC)
        except Exception as exc:
            append_event({"event": "daemon_error", "error": str(exc)[:200]})
            logger.exception("midrange_vol loop: %s", exc)
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    run_loop()
