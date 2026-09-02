"""Polymarket CLOB order placement for BTC 5m bot."""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

WEATHER_PY = "/root/weather-bot/venv/bin/python3"
PLACE_SCRIPT = "/root/btc-5m-bot/scripts/clob_place_order.py"
SELL_SCRIPT = "/root/btc-5m-bot/scripts/clob_sell_order.py"
GET_ORDER_SCRIPT = "/root/btc-5m-bot/scripts/clob_get_order.py"
CANCEL_SCRIPT = "/root/btc-5m-bot/scripts/clob_cancel_order.py"


def _order_signature_type() -> int:
    return int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "3"))


def _run_json(cmd: list[str], *, timeout: float = 60) -> dict[str, Any]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(proc.stderr.strip() or "CLOB command failed")
    result = json.loads(proc.stdout)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result


def live_execution_mode() -> str:
    """gtc_fok (default) | fok_only | gtc_only"""
    return os.getenv("LIVE_EXECUTION_MODE", "gtc_fok").strip().lower()


def fok_fallback_enabled() -> bool:
    mode = live_execution_mode()
    if mode == "gtc_only":
        return False
    if mode == "fok_only":
        return True
    return os.getenv("LIVE_FOK_FALLBACK", "1").lower() not in ("0", "false", "no")


def compute_max_fill_price(decision_ask: float, strategy_max_ask: float) -> float:
    """Worst acceptable buy price. Aggressive mode uses full strategy max_ask."""
    cap = float(strategy_max_ask)
    if os.getenv("LIVE_FILL_TO_MAX_ASK", "1").lower() in ("1", "true", "yes"):
        return cap
    slippage = float(os.getenv("LIVE_MAX_SLIPPAGE", "0.10"))
    return min(cap, round(float(decision_ask) + slippage, 4))


def verify_order_signing(*, token_id: str, price: float, size: float) -> bool:
    """Build + sign order locally (no POST). Proves signature_type mapping works."""
    from dotenv import load_dotenv

    load_dotenv("/root/weather-bot/.env")

    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import ApiCreds, OrderArgs
    from py_clob_client_v2.order_builder.constants import BUY

    key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    funder = os.getenv("POLYMARKET_WALLET_ADDRESS", "") or os.getenv(
        "POLYMARKET_DEPOSIT_ADDRESS", ""
    )
    creds = ApiCreds(
        api_key=os.getenv("POLYMARKET_API_KEY", ""),
        api_secret=os.getenv("POLYMARKET_SECRET", ""),
        api_passphrase=os.getenv("POLYMARKET_PASSPHRASE", ""),
    )
    client = ClobClient(
        "https://clob.polymarket.com",
        key=key,
        chain_id=137,
        creds=creds,
        signature_type=_order_signature_type(),
        funder=funder,
    )
    signed = client.create_order(
        OrderArgs(token_id=token_id, price=price, size=size, side=BUY)
    )
    return bool(signed)


def fetch_order_status(order_id: str) -> dict[str, Any] | None:
    if not order_id:
        return None
    try:
        return _run_json([WEATHER_PY, GET_ORDER_SCRIPT, "--order-id", order_id], timeout=30)
    except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


def cancel_order(order_id: str) -> dict[str, Any] | None:
    if not order_id:
        return None
    try:
        return _run_json([WEATHER_PY, CANCEL_SCRIPT, "--order-id", order_id], timeout=30)
    except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None


def fetch_live_best_bid(token_id: str) -> float | None:
    from polymarket_btc import fetch_order_book, summarize_book

    book = summarize_book(fetch_order_book(token_id))
    bid = book.get("best_bid")
    return float(bid) if bid is not None else None


def place_sell_order(
    *,
    token_id: str,
    shares: float,
    min_price: float,
    tick_size: str = "0.01",
    neg_risk: bool = False,
) -> dict[str, Any]:
    cmd = [
        WEATHER_PY,
        SELL_SCRIPT,
        "--token-id",
        token_id,
        "--shares",
        str(shares),
        "--price",
        str(min_price),
        "--tick-size",
        tick_size,
    ]
    if neg_risk:
        cmd.append("--neg-risk")
    return _run_json(cmd, timeout=60)


def place_live_sell_fok(
    *,
    token_id: str,
    shares: float,
    decision_bid: float,
    min_fill_price: float | None = None,
    tick_size: str = "0.01",
    neg_risk: bool = False,
) -> dict[str, Any]:
    """FOK sell shares at or above min_fill_price (defaults to decision_bid - slippage)."""
    tick = float(tick_size or 0.01)
    floor = min_fill_price if min_fill_price is not None else max(tick, decision_bid - float(os.getenv("CLOB_SELL_SLIPPAGE", "0.03")))
    floor = _round_tick(floor, tick)
    order = place_sell_order(
        token_id=token_id,
        shares=shares,
        min_price=floor,
        tick_size=tick_size,
        neg_risk=neg_risk,
    )
    if not _order_filled(order):
        order_id = order.get("orderID") or order.get("order_id")
        if order_id:
            fetched = fetch_order_status(order_id)
            if fetched:
                order = _merge_orders(order, fetched)
    if not _order_filled(order):
        raise RuntimeError(f"FOK sell not filled for {shares:.2f} sh @ floor {floor:.2f}")
    order["decision_bid"] = decision_bid
    order["fill_confirmed"] = True
    order["execution_path"] = "FOK_SELL"
    return order


def fetch_live_best_ask(token_id: str) -> float | None:
    from polymarket_btc import fetch_order_book, summarize_book

    book = summarize_book(fetch_order_book(token_id))
    ask = book.get("best_ask")
    return float(ask) if ask is not None else None


def _round_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 4)


def _order_filled(order: dict | None) -> bool:
    if not order:
        return False
    for key in ("makingAmount", "making_amount"):
        raw = order.get(key)
        if raw is not None and str(raw).strip() and float(raw) > 0:
            return True
    for key in ("takingAmount", "taking_amount"):
        raw = order.get(key)
        if raw is not None and str(raw).strip() and float(raw) > 0:
            return True
    matched = order.get("size_matched") or order.get("sizeMatched")
    if matched is not None and float(matched) > 0:
        return True
    status = str(order.get("status") or "").upper()
    return status in ("MATCHED", "FILLED")


def _merge_orders(*orders: dict | None) -> dict:
    out: dict[str, Any] = {}
    for order in orders:
        if order:
            out.update(order)
    return out


def _annotate_fill(order: dict, *, decision_ask: float, live_ask: float, path: str) -> dict:
    order["decision_ask"] = decision_ask
    order["live_ask_at_fill"] = live_ask
    order["execution_path"] = path
    order["fill_confirmed"] = True
    return order


def _price_within_cap(live_ask: float, decision_ask: float, max_fill_price: float, tick: float) -> float:
    limit_price = _round_tick(max(decision_ask, live_ask), tick)
    if limit_price > max_fill_price + 1e-9:
        raise RuntimeError(
            f"live ask {live_ask:.2f} above max fill {max_fill_price:.2f} (decision {decision_ask:.2f})"
        )
    return limit_price


def place_buy_order(
    *,
    token_id: str,
    price: float,
    stake_usd: float,
    tick_size: str = "0.01",
    neg_risk: bool = False,
    min_size: float = 5.0,
    use_limit: bool = True,
) -> dict[str, Any]:
    cmd = [
        WEATHER_PY,
        PLACE_SCRIPT,
        "--token-id",
        token_id,
        "--price",
        str(price),
        "--stake",
        str(stake_usd),
        "--tick-size",
        tick_size,
        "--min-size",
        str(min_size),
    ]
    if neg_risk:
        cmd.append("--neg-risk")
    if use_limit:
        cmd.append("--limit")
    return _run_json(cmd, timeout=60)


def place_fok_buy_chase(
    *,
    token_id: str,
    decision_ask: float,
    stake_usd: float,
    max_fill_price: float,
    tick_size: str = "0.01",
    neg_risk: bool = False,
    min_size: float = 5.0,
    deadline_ts: float | None = None,
) -> dict[str, Any]:
    """FOK market buy — chase live best ask within cap until filled or attempts exhausted."""
    tick = float(tick_size or 0.01)
    end = deadline_ts or (time.time() + float(os.getenv("LIVE_FILL_TIMEOUT_SEC", "35")))
    max_attempts = int(os.getenv("LIVE_FOK_MAX_ATTEMPTS", "10"))
    pause = float(os.getenv("LIVE_FOK_RETRY_SEC", "0.5"))
    attempts = 0
    last_error = "FOK not filled"

    while time.time() < end and attempts < max_attempts:
        live_ask = fetch_live_best_ask(token_id)
        if live_ask is None:
            raise RuntimeError("no live ask on order book")
        if live_ask > max_fill_price + 1e-9:
            raise RuntimeError(
                f"live ask {live_ask:.2f} above max fill {max_fill_price:.2f} (decision {decision_ask:.2f})"
            )
        # FOK sweeps the book up to max_fill_price (worst acceptable price).
        fok_price = _round_tick(max_fill_price, tick)

        try:
            order = place_buy_order(
                token_id=token_id,
                price=fok_price,
                stake_usd=stake_usd,
                tick_size=tick_size,
                neg_risk=neg_risk,
                min_size=min_size,
                use_limit=False,
            )
        except RuntimeError as exc:
            last_error = str(exc)
            attempts += 1
            time.sleep(pause)
            continue

        attempts += 1
        order_id = order.get("orderID") or order.get("order_id")
        if not _order_filled(order) and order_id:
            time.sleep(0.35)
            fetched = fetch_order_status(order_id)
            if fetched:
                order = _merge_orders(order, fetched)

        if _order_filled(order):
            return _annotate_fill(order, decision_ask=decision_ask, live_ask=live_ask, path="FOK")

        last_error = f"FOK attempt {attempts} cap {fok_price:.2f} live {live_ask:.2f} not filled"
        time.sleep(pause)

    raise RuntimeError(last_error)


def place_limit_buy_confirmed(
    *,
    token_id: str,
    decision_ask: float,
    stake_usd: float,
    max_fill_price: float,
    tick_size: str = "0.01",
    neg_risk: bool = False,
    min_size: float = 5.0,
    deadline_ts: float | None = None,
    allow_fok_fallback: bool = True,
) -> dict[str, Any]:
    """GTC limit buy at live best ask, poll until filled or deadline. Optional FOK fallback."""
    tick = float(tick_size or 0.01)
    poll_sec = float(os.getenv("LIVE_FILL_POLL_SEC", "2"))
    timeout_sec = float(os.getenv("LIVE_FILL_TIMEOUT_SEC", "25"))
    end = deadline_ts or (time.time() + timeout_sec)

    active_id: str | None = None
    last_post: dict[str, Any] | None = None
    attempts = 0
    max_attempts = int(os.getenv("LIVE_FILL_MAX_ATTEMPTS", "6"))

    while time.time() < end and attempts < max_attempts:
        live_ask = fetch_live_best_ask(token_id)
        if live_ask is None:
            raise RuntimeError("no live ask on order book")

        limit_price = _price_within_cap(live_ask, decision_ask, max_fill_price, tick)

        if active_id:
            cancel_order(active_id)
            time.sleep(0.4)
            fetched = fetch_order_status(active_id)
            if _order_filled(fetched):
                merged = _merge_orders(last_post, fetched)
                merged["limit_price"] = limit_price
                return _annotate_fill(merged, decision_ask=decision_ask, live_ask=live_ask, path="GTC")
            active_id = None

        last_post = place_buy_order(
            token_id=token_id,
            price=limit_price,
            stake_usd=stake_usd,
            tick_size=tick_size,
            neg_risk=neg_risk,
            min_size=min_size,
            use_limit=True,
        )
        attempts += 1
        active_id = last_post.get("orderID") or last_post.get("order_id")

        if _order_filled(last_post):
            return _annotate_fill(last_post, decision_ask=decision_ask, live_ask=live_ask, path="GTC")

        wait_until = min(time.time() + poll_sec, end)
        while time.time() < wait_until:
            if not active_id:
                break
            fetched = fetch_order_status(active_id)
            if _order_filled(fetched):
                merged = _merge_orders(last_post, fetched)
                return _annotate_fill(merged, decision_ask=decision_ask, live_ask=live_ask, path="GTC")
            time.sleep(0.5)

    if active_id:
        cancel_order(active_id)
        fetched = fetch_order_status(active_id)
        if _order_filled(fetched):
            merged = _merge_orders(last_post, fetched)
            return _annotate_fill(merged, decision_ask=decision_ask, live_ask=live_ask, path="GTC")

    if allow_fok_fallback and fok_fallback_enabled():
        fok_deadline = max(time.time() + 8.0, end)
        order = place_fok_buy_chase(
            token_id=token_id,
            decision_ask=decision_ask,
            stake_usd=stake_usd,
            max_fill_price=max_fill_price,
            tick_size=tick_size,
            neg_risk=neg_risk,
            min_size=min_size,
            deadline_ts=fok_deadline,
        )
        order["execution_path"] = "GTC→FOK"
        return order

    raise RuntimeError("GTC limit order not filled before deadline")


def place_live_buy_confirmed(
    *,
    token_id: str,
    decision_ask: float,
    stake_usd: float,
    max_fill_price: float,
    tick_size: str = "0.01",
    neg_risk: bool = False,
    min_size: float = 5.0,
    deadline_ts: float | None = None,
) -> dict[str, Any]:
    """Route live entry by LIVE_EXECUTION_MODE."""
    mode = live_execution_mode()
    if mode == "fok_only":
        return place_fok_buy_chase(
            token_id=token_id,
            decision_ask=decision_ask,
            stake_usd=stake_usd,
            max_fill_price=max_fill_price,
            tick_size=tick_size,
            neg_risk=neg_risk,
            min_size=min_size,
            deadline_ts=deadline_ts,
        )
    return place_limit_buy_confirmed(
        token_id=token_id,
        decision_ask=decision_ask,
        stake_usd=stake_usd,
        max_fill_price=max_fill_price,
        tick_size=tick_size,
        neg_risk=neg_risk,
        min_size=min_size,
        deadline_ts=deadline_ts,
        allow_fok_fallback=fok_fallback_enabled(),
    )
