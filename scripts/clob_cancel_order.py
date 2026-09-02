#!/usr/bin/env python3
"""Cancel a CLOB order by ID (stdout JSON)."""

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv("/root/weather-bot/.env")

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, OrderPayload

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--order-id", required=True)
    args = p.parse_args()

    key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    funder = os.getenv("POLYMARKET_WALLET_ADDRESS", "") or os.getenv("POLYMARKET_DEPOSIT_ADDRESS", "")
    creds = ApiCreds(
        api_key=os.getenv("POLYMARKET_API_KEY", ""),
        api_secret=os.getenv("POLYMARKET_SECRET", ""),
        api_passphrase=os.getenv("POLYMARKET_PASSPHRASE", ""),
    )
    sig_env = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "3"))

    client = ClobClient(
        HOST,
        key=key,
        chain_id=CHAIN_ID,
        creds=creds,
        signature_type=sig_env,
        funder=funder,
    )
    client.set_api_creds(creds if creds.api_key else client.create_or_derive_api_key())
    resp = client.cancel_order(OrderPayload(orderID=args.order_id))
    print(json.dumps(resp if isinstance(resp, dict) else {"response": str(resp), "orderID": args.order_id}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc), "success": False}))
        sys.exit(1)
