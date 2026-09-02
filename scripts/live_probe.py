#!/usr/bin/env python3
"""
One-off LIVE order probe — tests CLOB placement without touching trade_log_full.json.

Usage:
  ./venv/bin/python3 scripts/live_probe.py --once
  ./venv/bin/python3 scripts/live_probe.py --once --stake 1 --dry-run

Records: logs/live_probe.jsonl only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os
import urllib.request

from btc_clob import place_buy_order, verify_order_signing
from polymarket_btc import (
    discover_current_market,
    fetch_event_by_slug,
    fetch_order_book,
    parse_market,
    seconds_to_end,
    summarize_book,
    window_start_ts,
)
from price_feeds import fetch_btc_usd

PROBE_LOG = ROOT / "logs" / "live_probe.jsonl"
HKT = ZoneInfo("Asia/Hong_Kong")

# Probe fires once per window in this T-remaining band (inside strategy entry window).
FIRE_T_MIN = 25.0
FIRE_T_MAX = 45.0
POLL_SEC = 2.0
MIN_VALID_ASK = 0.12
MAX_VALID_ASK = 0.88


def log(msg: str) -> None:
    print(msg, flush=True)


def check_geoblock() -> dict:
    try:
        with urllib.request.urlopen("https://polymarket.com/api/geoblock", timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        return {"blocked": None, "error": str(exc)}


def ask_is_valid(ask: float | None) -> bool:
    return bool(ask and MIN_VALID_ASK <= float(ask) <= MAX_VALID_ASK)


def now_hkt() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S")


def load_completed_probe_slugs() -> set[str]:
    if not PROBE_LOG.exists():
        return set()
    slugs: set[str] = set()
    terminal = {"submitted", "failed", "geoblocked", "dry_run"}
    for line in PROBE_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if row.get("slug") and row.get("status") in terminal:
                slugs.add(row["slug"])
        except json.JSONDecodeError:
            continue
    return slugs


def append_probe(row: dict) -> None:
    PROBE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PROBE_LOG.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def pick_side_and_ask(
    *,
    open_btc: float,
    current_btc: float,
    up_book: dict,
    down_book: dict,
    target_stake: float,
) -> dict:
    """Momentum side (strategy judgment); prefer ~$1 fill after min 5 shares."""
    delta = current_btc - open_btc
    momentum = "up" if delta >= 0 else "down"

    def side_info(side: str) -> dict:
        book = up_book if side == "up" else down_book
        ask = book.get("best_ask")
        est = round(float(ask) * 5, 2) if ask else None
        return {
            "side": side,
            "ask": ask,
            "depth": book.get("ask_depth_usd"),
            "spread": book.get("spread"),
            "est_stake_min_shares": est,
        }

    primary = side_info(momentum)
    other = side_info("down" if momentum == "up" else "up")

    # Follow momentum unless invalid ask; keep stake near target when possible.
    chosen = primary
    reason = f"momentum delta=${delta:+.2f} → {momentum.upper()}"
    if not ask_is_valid(primary.get("ask")):
        if ask_is_valid(other.get("ask")):
            chosen = other
            reason = (
                f"momentum {momentum.upper()} ask invalid "
                f"({primary.get('ask')}); fallback {other['side'].upper()}"
            )
        else:
            chosen = primary
            reason = f"no valid ask on either side (up={primary.get('ask')} down={other.get('ask')})"
    elif (
        primary.get("est_stake_min_shares")
        and primary["est_stake_min_shares"] > target_stake * 2
        and ask_is_valid(other.get("ask"))
        and other.get("est_stake_min_shares")
        and other["est_stake_min_shares"] <= target_stake * 2
    ):
        chosen = other
        reason = (
            f"momentum {momentum.upper()} ask={primary['ask']:.2f} too costly "
            f"(~${primary['est_stake_min_shares']:.2f} min); probe cheaper {other['side'].upper()}"
        )
    elif not ask_is_valid(chosen.get("ask")):
        valid = [s for s in (primary, other) if ask_is_valid(s.get("ask"))]
        if valid:
            chosen = min(valid, key=lambda s: float(s["ask"]))
            reason = f"probe cheapest valid ask → {chosen['side'].upper()} @ {chosen['ask']:.2f}"

    return {**chosen, "delta_usd": round(delta, 2), "reason": reason}


def run_probe(*, stake: float, dry_run: bool) -> dict:
    probed = load_completed_probe_slugs()
    open_btc: float | None = None
    open_slug: str | None = None

    geo = check_geoblock()
    log(
        f"[live_probe] waiting for window T={FIRE_T_MAX:.0f}~{FIRE_T_MIN:.0f}s "
        f"(stake target ${stake:.2f}) geoblock={geo.get('blocked')} country={geo.get('country')}"
    )
    while True:
        market = discover_current_market()
        if not market:
            time.sleep(POLL_SEC)
            continue

        slug = market.slug
        t_rem = seconds_to_end(market)

        if slug != open_slug:
            open_slug = slug
            open_btc = fetch_btc_usd()["price"]
            log(f"[live_probe] new window {slug} open_btc=${open_btc:.2f} t={t_rem:.0f}s")

        if slug in probed:
            time.sleep(POLL_SEC)
            continue

        if not (FIRE_T_MIN <= t_rem <= FIRE_T_MAX):
            time.sleep(POLL_SEC)
            continue

        if open_btc is None:
            open_btc = fetch_btc_usd()["price"]

        btc = fetch_btc_usd()
        up_book = summarize_book(fetch_order_book(market.up_token_id))
        down_book = summarize_book(fetch_order_book(market.down_token_id))
        pick = pick_side_and_ask(
            open_btc=open_btc,
            current_btc=btc["price"],
            up_book=up_book,
            down_book=down_book,
            target_stake=stake,
        )

        side = pick["side"]
        ask = pick.get("ask")
        row: dict = {
            "probe_id": f"probe-{int(time.time())}",
            "timestamp_hkt": now_hkt(),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "slug": slug,
            "title": market.title,
            "t_remaining": round(t_rem, 1),
            "open_btc": open_btc,
            "btc_price": btc["price"],
            "side": side,
            "entry_ask": ask,
            "target_stake_usd": stake,
            "pick_reason": pick["reason"],
            "delta_usd": pick["delta_usd"],
            "dry_run": dry_run,
            "status": "pending",
            "geoblock": geo,
        }

        if not ask_is_valid(ask):
            row["status"] = "skipped_bad_book"
            row["error"] = f"invalid ask on chosen side: {ask}"
            append_probe(row)
            log(f"[live_probe] bad book {slug} ask={ask}; wait next window")
            time.sleep(POLL_SEC)
            continue

        token_id = market.up_token_id if side == "up" else market.down_token_id
        event = fetch_event_by_slug(slug)
        mk = (event.get("markets") or [{}])[0] if event else {}
        tick = str(mk.get("tickSize") or "0.01")
        neg_risk = bool(mk.get("negRisk", False))
        min_size = float(mk.get("orderMinSize") or 5)

        est_stake = round(float(ask) * max(min_size, round(stake / ask, 2)), 2)
        row["est_stake_usd"] = est_stake

        log(
            f"[live_probe] FIRE {slug} {side.upper()} @ {ask:.2f} "
            f"target=${stake:.2f} est_min=${est_stake:.2f} | {pick['reason']}"
        )

        try:
            row["sign_verified"] = verify_order_signing(
                token_id=token_id,
                price=float(ask),
                size=max(min_size, round(stake / float(ask), 2)),
            )
            log("[live_probe] sign_verified=True (signature_type fix OK)")
        except Exception as exc:
            row["sign_verified"] = False
            row["status"] = "failed"
            row["error"] = f"sign failed: {exc}"
            append_probe(row)
            log(f"[live_probe] SIGN FAILED: {exc}")
            return row

        if dry_run:
            row["status"] = "dry_run"
            append_probe(row)
            log("[live_probe] dry-run complete (no order sent)")
            return row

        if geo.get("blocked"):
            row["status"] = "geoblocked"
            row["error"] = (
                f"POST skipped: trading restricted in {geo.get('country')} "
                f"(IP {geo.get('ip')}). Signature fix verified; need non-blocked IP to submit."
            )
            append_probe(row)
            log(f"[live_probe] GEOBLOCKED {geo.get('country')}: sign OK, POST skipped")
            return row

        try:
            order = place_buy_order(
                token_id=token_id,
                price=float(ask),
                stake_usd=stake,
                tick_size=tick,
                neg_risk=neg_risk,
                min_size=min_size,
            )
            row["status"] = "submitted"
            row["order"] = order
            row["order_id"] = order.get("orderID") or order.get("order_id")
            row["stake_usd"] = order.get("stake_usd")
            row["size"] = order.get("size")
            row["limit_price"] = order.get("limit_price")
            append_probe(row)
            log(f"[live_probe] SUCCESS order_id={row.get('order_id')} stake=${row.get('stake_usd')}")
            return row
        except Exception as exc:
            row["status"] = "failed"
            row["error"] = str(exc)
            append_probe(row)
            log(f"[live_probe] FAILED: {exc}")
            return row


def main() -> None:
    p = argparse.ArgumentParser(description="LIVE CLOB probe (separate log, ~$1 test)")
    p.add_argument("--once", action="store_true", help="Fire on next eligible window then exit")
    p.add_argument("--stake", type=float, default=1.0, help="Target stake USD (min 5 shares may cost more)")
    p.add_argument("--dry-run", action="store_true", help="Decision only, no API order")
    args = p.parse_args()

    if not args.once:
        print("Use --once to run a single probe on the next eligible window.")
        sys.exit(1)

    row = run_probe(stake=args.stake, dry_run=args.dry_run)
    ok = row.get("status") in ("submitted", "dry_run") or (
        row.get("status") == "geoblocked" and row.get("sign_verified")
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
