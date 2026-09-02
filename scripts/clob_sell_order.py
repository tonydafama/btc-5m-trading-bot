#!/usr/bin/env python3
"""Place a CLOB SELL on Polymarket (stdout JSON). Default: FOK market sell by shares."""

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
    OrderType,
    PartialCreateOrderOptions,
)
from py_clob_client_v2.order_builder.constants import SELL

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--token-id", required=True)
    p.add_argument("--shares", type=float, required=True)
    p.add_argument("--price", type=float, required=True, help="Worst acceptable bid (FOK floor)")
    p.add_argument("--tick-size", default="0.01")
    p.add_argument("--neg-risk", action="store_true")
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
    shares = round(float(args.shares), 2)
    if shares <= 0:
        raise ValueError("shares must be positive")

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

    mo = MarketOrderArgs(
        token_id=args.token_id,
        amount=shares,
        side=SELL,
        price=price,
        order_type=OrderType.FOK,
    )
    resp = client.create_and_post_market_order(mo, options=options, order_type=OrderType.FOK)
    out = resp if isinstance(resp, dict) else {"response": str(resp)}
    out["limit_price"] = price
    out["size"] = shares
    out["order_type"] = "FOK_SELL"
    raw_proceeds = out.get("takingAmount") or out.get("taking_amount")
    if raw_proceeds is not None:
        try:
            out["proceeds_usd"] = round(float(raw_proceeds), 4)
        except (TypeError, ValueError):
            pass
    print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc), "success": False}))
        sys.exit(1)
