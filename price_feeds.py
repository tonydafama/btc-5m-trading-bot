"""BTC price feeds: Binance spot (signal) + CoinGecko fallback."""

from __future__ import annotations

import requests

from bot_common import retry_api

HEADERS = {"User-Agent": "btc-5m-bot/1.0"}


@retry_api()
def fetch_binance_btc() -> float:
    resp = requests.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": "BTCUSDT"},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return float(resp.json()["price"])


@retry_api()
def fetch_coingecko_btc() -> float:
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "bitcoin", "vs_currencies": "usd"},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return float(resp.json()["bitcoin"]["usd"])


def fetch_btc_usd() -> dict:
    """Return BTC/USD with source label."""
    try:
        px = fetch_binance_btc()
        return {"price": px, "source": "binance"}
    except Exception:
        px = fetch_coingecko_btc()
        return {"price": px, "source": "coingecko"}


@retry_api()
def fetch_binance_eth() -> float:
    resp = requests.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": "ETHUSDT"},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return float(resp.json()["price"])


@retry_api()
def fetch_coingecko_eth() -> float:
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "ethereum", "vs_currencies": "usd"},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return float(resp.json()["ethereum"]["usd"])


def fetch_eth_usd() -> dict:
    """Return ETH/USD with source label."""
    try:
        px = fetch_binance_eth()
        return {"price": px, "source": "binance"}
    except Exception:
        px = fetch_coingecko_eth()
        return {"price": px, "source": "coingecko"}


@retry_api()
def fetch_binance_sol() -> float:
    resp = requests.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": "SOLUSDT"},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return float(resp.json()["price"])


def fetch_sol_usd() -> dict:
    """Return SOL/USD with source label."""
    try:
        px = fetch_binance_sol()
        return {"price": px, "source": "binance"}
    except Exception:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        return {"price": float(resp.json()["solana"]["usd"]), "source": "coingecko"}


@retry_api()
def fetch_binance_klines(interval: str = "1m", limit: int = 10) -> list[list]:
    resp = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": interval, "limit": limit},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def recent_momentum(minutes: int = 5) -> dict:
    """Recent Binance BTC move — used to detect reversal vs window-open delta."""
    kl = fetch_binance_klines("1m", max(minutes, 3))
    if len(kl) < 2:
        return {"move_usd": 0.0, "range_usd": 0.0, "direction": "flat", "chop_ratio": 0.0}
    closes = [float(k[4]) for k in kl[-minutes:]]
    highs = [float(k[2]) for k in kl[-minutes:]]
    lows = [float(k[3]) for k in kl[-minutes:]]
    move = closes[-1] - closes[0]
    rng = max(highs) - min(lows)
    direction = "up" if move > 0 else ("down" if move < 0 else "flat")
    chop = (rng / abs(move)) if abs(move) >= 1 else (999.0 if rng > 20 else 0.0)
    return {
        "move_usd": round(move, 2),
        "range_usd": round(rng, 2),
        "direction": direction,
        "chop_ratio": round(chop, 2),
    }
