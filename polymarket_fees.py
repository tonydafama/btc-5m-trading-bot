"""Polymarket crypto taker fee estimates (5m / 15m markets)."""

from __future__ import annotations

CRYPTO_TAKER_FEE_RATE = 0.07


def estimate_taker_fee(shares: float, price: float) -> float:
    if shares <= 0 or price <= 0:
        return 0.0
    return round(shares * CRYPTO_TAKER_FEE_RATE * price * (1 - price), 4)
