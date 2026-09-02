#!/usr/bin/env python3
"""Analyze BTC paper losses vs Binance klines — find skip patterns."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path("/root/btc-5m-bot")
load_dotenv(ROOT / ".env")
TRADES = ROOT / "logs" / "paper_trades.json"
OUT = ROOT / "logs" / "loss_analysis.json"


def kline_at(ts_str: str) -> dict | None:
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        start_ms = int(dt.timestamp() * 1000) - 5 * 60 * 1000
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1m", "startTime": start_ms, "limit": 8},
            timeout=15,
        )
        if not r.ok:
            return None
        kl = r.json()
        if len(kl) < 3:
            return None
        closes = [float(k[4]) for k in kl]
        highs = [float(k[2]) for k in kl]
        lows = [float(k[3]) for k in kl]
        move = closes[-1] - closes[0]
        rng = max(highs) - min(lows)
        return {"move_5m": round(move, 2), "range_5m": round(rng, 2)}
    except Exception:
        return None


def main() -> int:
    trades = json.loads(TRADES.read_text()) if TRADES.exists() else []
    settled = [t for t in trades if t.get("status") == "settled"]
    losses = [t for t in settled if not t.get("won")]
    wins = [t for t in settled if t.get("won")]

    loss_patterns = Counter()
    win_patterns = Counter()

    for t in losses:
        side = t.get("side", "")
        delta = abs(float(t.get("delta_usd") or 0))
        ask = float(t.get("entry_price") or 0)
        kl = kline_at(t.get("timestamp", ""))
        if kl:
            rev = (kl["move_5m"] > 0 and side == "down") or (kl["move_5m"] < 0 and side == "up")
            if rev:
                loss_patterns["momentum_reversal"] += 1
            chop = kl["range_5m"] / abs(kl["move_5m"]) if abs(kl["move_5m"]) >= 1 else 99
            if chop >= 4:
                loss_patterns["choppy_whipsaw"] += 1
        if delta < 20:
            loss_patterns["weak_window_move_lt_20"] += 1
        if ask <= 0.55:
            loss_patterns["underdog_entry"] += 1
        if ask >= 0.80:
            loss_patterns["expensive_favorite"] += 1

    for t in wins:
        side = t.get("side", "")
        kl = kline_at(t.get("timestamp", ""))
        if kl:
            rev = (kl["move_5m"] > 0 and side == "down") or (kl["move_5m"] < 0 and side == "up")
            if rev:
                win_patterns["momentum_reversal"] += 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trades_settled": len(settled),
        "losses": len(losses),
        "wins": len(wins),
        "loss_rate_pct": round(len(losses) / len(settled) * 100, 1) if settled else 0,
        "loss_patterns": dict(loss_patterns.most_common()),
        "win_patterns": dict(win_patterns.most_common()),
        "recommendations": [
            "Skip when 5m Binance move opposes bet side (momentum_reversal)",
            "Raise MIN_BTC_MOVE_USD to 18+ (weak moves lose more)",
            "Require extra move for underdog entries (ask <= 0.55)",
            "Skip choppy windows (high range / low net move)",
        ],
        "note": "Observation only — NOT applied as live entry filters.",
        "active_entry_filters": {
            "MAX_ENTRY_ASK": float(os.getenv("MAX_ENTRY_ASK", "0.60")),
            "MAX_SPREAD": float(os.getenv("MAX_SPREAD", "0.06")),
            "MIN_DEPTH_USD": float(os.getenv("MIN_DEPTH_USD", "50")),
            "MIN_BTC_MOVE_USD": float(os.getenv("MIN_BTC_MOVE_USD", "12")),
        },
        "loss_pattern_filters_disabled": {
            "USE_LOSS_PATTERN_FILTERS": False,
            "REQUIRE_MOMENTUM_ALIGN": False,
            "MAX_CHOP_RATIO": None,
            "UNDERDOG_MIN_MOVE_USD": None,
        },
    }
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
