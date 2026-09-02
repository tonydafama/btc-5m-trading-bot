#!/usr/bin/env python3
"""Derive Polymarket API creds for Bot #2 and verify wallet readiness."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path("/root/btc-5m-bot")
sys.path.insert(0, str(ROOT))

ENV_FILE = ROOT / ".env.bot2"
EXAMPLE = ROOT / ".env.bot2.example"


def _load_env_lines() -> list[str]:
    if ENV_FILE.exists():
        return ENV_FILE.read_text().splitlines()
    if EXAMPLE.exists():
        return EXAMPLE.read_text().splitlines()
    raise SystemExit(f"Missing {ENV_FILE} — copy from {EXAMPLE.name}")


def _set_kv(lines: list[str], key: str, value: str) -> list[str]:
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    return out


def main() -> None:
    lines = _load_env_lines()
    pk = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    if not pk:
        for line in lines:
            if line.startswith("POLYMARKET_PRIVATE_KEY="):
                pk = line.split("=", 1)[1].strip()
                break
    if not pk:
        raise SystemExit(
            "Set POLYMARKET_PRIVATE_KEY in .env.bot2 (MetaMask export) then re-run."
        )

    funder = ""
    for line in lines:
        if line.startswith("POLYMARKET_WALLET_ADDRESS="):
            funder = line.split("=", 1)[1].strip()
            break
    if not funder:
        for line in lines:
            if line.startswith("POLYMARKET_DEPOSIT_ADDRESS="):
                funder = line.split("=", 1)[1].strip()
                break

    sig = 0
    for line in lines:
        if line.startswith("POLYMARKET_SIGNATURE_TYPE="):
            sig = int(line.split("=", 1)[1].strip() or "0")
            break

    from py_clob_client_v2.client import ClobClient

    client = ClobClient(
        "https://clob.polymarket.com",
        key=pk,
        chain_id=137,
        signature_type=sig,
        funder=funder or None,
    )
    creds = client.create_or_derive_api_key()
    lines = _set_kv(lines, "POLYMARKET_PRIVATE_KEY", pk)
    if funder:
        lines = _set_kv(lines, "POLYMARKET_WALLET_ADDRESS", funder)
        lines = _set_kv(lines, "POLYMARKET_DEPOSIT_ADDRESS", funder)
    lines = _set_kv(lines, "POLYMARKET_API_KEY", creds.api_key)
    lines = _set_kv(lines, "POLYMARKET_SECRET", creds.api_secret)
    lines = _set_kv(lines, "POLYMARKET_PASSPHRASE", creds.api_passphrase)
    lines = _set_kv(lines, "POLYMARKET_SIGNATURE_TYPE", str(sig))
    lines = _set_kv(lines, "BOT_INSTANCE", "bot2")
    lines = _set_kv(lines, "TRADING_MODE", "live")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_FILE, 0o600)
    print(f"Wrote API credentials to {ENV_FILE}")

    os.environ["BOT_INSTANCE"] = "bot2"
    os.environ["BOT_ENV_FILE"] = str(ENV_FILE)
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=True)
    os.environ["WALLET_CACHE_FILE"] = str(ROOT / "logs" / "bot2" / "wallet_cache.json")

    from polymarket_wallet import fetch_wallet_status

    status = fetch_wallet_status(force=True)
    cash = float(status.get("cash_usdc") or 0)
    ready = bool(status.get("api_ready"))
    print(f"Wallet cash: ${cash:.2f}")
    print(f"API ready: {ready} — {status.get('ready_label')}")
    if status.get("ready_detail"):
        print(f"Detail: {status.get('ready_detail')}")
    if not ready:
        print("\nNext: fund USDC on Polygon + enable Polymarket trading allowances, then re-run.")
    elif cash < 50:
        print("\nWarning: balance low for Alpha-Max live (recommend $300–500+).")


if __name__ == "__main__":
    main()
