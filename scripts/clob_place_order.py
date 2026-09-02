#!/usr/bin/env python3
"""Place a CLOB BUY on Polymarket (stdout JSON). Default: FOK market buy."""

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv("/root/weather-bot/.env")

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    ApiCreds,
    MarketOrderArgs,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
)
from py_clob_client_v2.order_builder.constants import BUY

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


def _attach_stake(out: dict, *, price: float, size: float) -> dict:
    raw_spent = out.get("makingAmount") or out.get("making_amount")
    if raw_spent is not None and str(raw_spent).strip():
        try:
            out["stake_usd"] = round(float(raw_spent), 2)
        except (TypeError, ValueError):
            out["stake_usd"] = round(price * size, 2)
    else:
        out["stake_usd"] = round(price * size, 2)
    out["stake_intended"] = round(price * size, 2)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--token-id", required=True)
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--stake", type=float, required=True)
    p.add_argument("--tick-size", default="0.01")
    p.add_argument("--neg-risk", action="store_true")
    p.add_argument("--min-size", type=float, default=5.0)
    p.add_argument(
        "--limit",
        action="store_true",
        help="GTC limit order (legacy). Default is FOK market buy.",
    )
    args = p.parse_args()

    key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    funder = os.getenv("POLYMARKET_WALLET_ADDRESS", "") or os.getenv("POLYMARKET_DEPOSIT_ADDRESS", "")
    creds = ApiCreds(
        api_key=os.getenv("POLYMARKET_API_KEY", ""),
        api_secret=os.getenv("POLYMARKET_SECRET", ""),
        api_passphrase=os.getenv("POLYMARKET_PASSPHRASE", ""),
    )
    sig_env = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "3"))

    tick = float(args.tick_size or 0.01)
    price = max(tick, min(1.0 - tick, round(round(args.price / tick) * tick, 4)))
    size = round(args.stake / price, 2)
    if size < args.min_size:
        size = args.min_size

    client = ClobClient(
        HOST,
        key=key,
        chain_id=CHAIN_ID,
        creds=creds,
        signature_type=sig_env,
        funder=funder,
    )
    client.set_api_creds(creds if creds.api_key else client.create_or_derive_api_key())
    options = PartialCreateOrderOptions(tick_size=args.tick_size, neg_risk=args.neg_risk)

    if args.limit:
        order = OrderArgs(token_id=args.token_id, price=price, size=size, side=BUY)
        resp = client.create_and_post_order(order, options=options)
        out = resp if isinstance(resp, dict) else {"response": str(resp)}
        out["limit_price"] = price
        out["size"] = size
        out["order_type"] = "GTC"
    else:
        mo = MarketOrderArgs(
            token_id=args.token_id,
            amount=args.stake,
            side=BUY,
            price=price,
            order_type=OrderType.FOK,
        )
        resp = client.create_and_post_market_order(mo, options=options, order_type=OrderType.FOK)
        out = resp if isinstance(resp, dict) else {"response": str(resp)}
        out["limit_price"] = price
        out["size"] = size
        out["order_type"] = "FOK"

    print(json.dumps(_attach_stake(out, price=price, size=size)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc), "success": False}))
        sys.exit(1)
