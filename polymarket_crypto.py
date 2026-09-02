"""Polymarket crypto Up/Down 5m markets — BTC, ETH, SOL, …"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from bot_common import retry_api

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
HEADERS = {"User-Agent": "btc-5m-bot/1.0"}
WINDOW_SEC = 300


@dataclass
class CryptoWindowMarket:
    asset: str
    slug: str
    title: str
    window_start_ts: int
    window_end_ts: int
    end_date: str
    up_token_id: str
    down_token_id: str
    up_price: float
    down_price: float
    accepting_orders: bool
    closed: bool


def window_start_ts(now: float | None = None) -> int:
    ts = int(now or time.time())
    return (ts // WINDOW_SEC) * WINDOW_SEC


def window_end_ts(start: int) -> int:
    return start + WINDOW_SEC


def slug_for(asset: str, start: int) -> str:
    return f"{asset.lower()}-updown-5m-{start}"


@retry_api()
def fetch_event_by_slug(slug: str) -> Optional[dict[str, Any]]:
    resp = requests.get(
        f"{GAMMA_URL}/events",
        params={"slug": slug},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0] if data else None


def parse_market(event: dict[str, Any], asset: str) -> Optional[CryptoWindowMarket]:
    markets = event.get("markets") or []
    if not markets:
        return None
    m = markets[0]
    tokens = json.loads(m.get("clobTokenIds") or "[]")
    outcomes = json.loads(m.get("outcomes") or "[]")
    prices = json.loads(m.get("outcomePrices") or "[0,0]")
    if len(tokens) < 2:
        return None

    up_idx = 0
    down_idx = 1
    for i, label in enumerate(outcomes):
        if str(label).lower() == "up":
            up_idx = i
        elif str(label).lower() == "down":
            down_idx = i

    slug = event.get("slug", "")
    try:
        start = int(slug.rsplit("-", 1)[-1])
    except ValueError:
        end_dt = datetime.fromisoformat(event["endDate"].replace("Z", "+00:00"))
        start = int(end_dt.timestamp()) - WINDOW_SEC

    return CryptoWindowMarket(
        asset=asset.lower(),
        slug=slug,
        title=event.get("title", ""),
        window_start_ts=start,
        window_end_ts=window_end_ts(start),
        end_date=event.get("endDate", ""),
        up_token_id=tokens[up_idx],
        down_token_id=tokens[down_idx],
        up_price=float(prices[up_idx]),
        down_price=float(prices[down_idx]),
        accepting_orders=bool(m.get("acceptingOrders")),
        closed=bool(event.get("closed")),
    )


def discover_current_market(asset: str = "btc", now: float | None = None) -> Optional[CryptoWindowMarket]:
    ts = int(now or time.time())
    base = window_start_ts(ts)
    asset = asset.lower()
    for offset in (0, 1, -1, 2):
        slug = slug_for(asset, base + offset * WINDOW_SEC)
        event = fetch_event_by_slug(slug)
        if not event:
            continue
        market = parse_market(event, asset)
        if market and not market.closed:
            return market
    return None


@retry_api()
def fetch_order_book(token_id: str) -> dict[str, Any]:
    resp = requests.get(
        f"{CLOB_URL}/book",
        params={"token_id": token_id},
        headers=HEADERS,
        timeout=10,
    )
    if resp.status_code == 404:
        return {"asks": [], "bids": []}
    resp.raise_for_status()
    return resp.json()


def summarize_book(book: dict[str, Any]) -> dict[str, Any]:
    asks = book.get("asks") or []
    bids = book.get("bids") or []

    def _levels(levels: list, reverse: bool) -> list[tuple[float, float]]:
        parsed = [(float(l["price"]), float(l["size"])) for l in levels if l.get("price")]
        return sorted(parsed, key=lambda x: x[0], reverse=reverse)

    ask_lvls = _levels(asks, reverse=False)
    bid_lvls = _levels(bids, reverse=True)

    best_ask = ask_lvls[0][0] if ask_lvls else None
    best_bid = bid_lvls[0][0] if bid_lvls else None
    ask_depth = sum(px * sz for px, sz in ask_lvls[:5])
    bid_depth = sum(px * sz for px, sz in bid_lvls[:5])
    spread = (best_ask - best_bid) if best_ask is not None and best_bid is not None else None

    return {
        "best_ask": best_ask,
        "best_bid": best_bid,
        "spread": round(spread, 4) if spread is not None else None,
        "ask_depth_usd": round(ask_depth, 2),
        "bid_depth_usd": round(bid_depth, 2),
        "ask_levels": len(ask_lvls),
        "bid_levels": len(bid_lvls),
    }


def seconds_to_end(market: CryptoWindowMarket, now: float | None = None) -> float:
    return market.window_end_ts - (now or time.time())


def fetch_market_outcome(slug: str) -> Optional[dict[str, Any]]:
    event = fetch_event_by_slug(slug)
    if not event:
        return None
    markets = event.get("markets") or []
    if not markets:
        return None
    m = markets[0]
    outcomes = json.loads(m.get("outcomes") or "[]")
    prices_raw = json.loads(m.get("outcomePrices") or "[]")
    if not outcomes or len(prices_raw) != len(outcomes):
        return None

    prices = [float(p) for p in prices_raw]
    winners: list[str] = []
    for label, price in zip(outcomes, prices):
        if price >= 0.99:
            winners.append(str(label).lower())

    if len(winners) != 1 or winners[0] not in ("up", "down"):
        return None

    return {
        "outcome": winners[0],
        "source": "polymarket",
        "closed": bool(event.get("closed")),
        "resolution_status": m.get("umaResolutionStatus"),
        "outcome_prices": prices_raw,
        "outcomes": outcomes,
    }
