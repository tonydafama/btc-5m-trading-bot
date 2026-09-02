"""Read weather-bot paper trades for dashboard API (no import from weather-bot)."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WEATHER_ROOT = Path("/root/weather-bot")
TRADES_FILE = WEATHER_ROOT / "logs" / "paper_trades.json"
WEATHER_ENV = WEATHER_ROOT / ".env"
HKT = ZoneInfo("Asia/Hong_Kong")


def _initial_capital() -> float:
    if WEATHER_ENV.exists():
        for line in WEATHER_ENV.read_text().splitlines():
            if line.startswith("INITIAL_CAPITAL="):
                try:
                    return float(line.split("=", 1)[1].strip())
                except ValueError:
                    break
    return float(os.getenv("WEATHER_INITIAL_CAPITAL", "200"))


def _trade_result(trade: dict) -> str:
    if trade.get("won") is True:
        return "WIN"
    if trade.get("won") is False:
        return "LOSS"
    pnl = trade.get("pnl")
    if pnl is not None:
        return "WIN" if float(pnl) >= 0 else "LOSS"
    return "PENDING"


def _is_settled(trade: dict) -> bool:
    return _trade_result(trade) in ("WIN", "LOSS")


def _load_trades() -> list[dict]:
    if not TRADES_FILE.exists():
        return []
    try:
        data = json.loads(TRADES_FILE.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _enrich_trade(trade: dict) -> dict:
    result = _trade_result(trade)
    pnl_raw = trade.get("pnl")
    pnl_val = float(pnl_raw) if pnl_raw is not None else None
    edge_pct = round(float(trade.get("edge", 0)) * 100, 1)
    market_yes = float(trade.get("market_prob", 0))
    win_prob = float(trade["win_prob"]) if trade.get("win_prob") is not None else (
        1.0 - market_yes if trade.get("side") == "NO" else market_yes
    )

    if result == "WIN":
        row_class = "row-win"
    elif result == "LOSS":
        row_class = "row-loss"
    else:
        row_class = "row-pending"

    if pnl_val is not None:
        pnl_display = f"{'+' if pnl_val >= 0 else ''}${pnl_val:.2f}"
        pnl_class = "green" if pnl_val >= 0 else "red"
    else:
        pnl_display = "—"
        pnl_class = "muted"

    ts = trade.get("timestamp", "")
    time_display = ts.split("T")[-1][:8] if "T" in ts else ts

    stake = float(trade.get("stake", 0))
    edge = float(trade.get("edge", 0))
    expected_return = trade.get("expected_return")
    if expected_return is None:
        expected_return = round(stake * edge, 2)
    else:
        expected_return = float(expected_return)

    return {
        **trade,
        "result": result,
        "row_class": row_class,
        "edge_pct": edge_pct,
        "win_prob_pct": round(win_prob * 100, 1),
        "expected_return": expected_return,
        "expected_return_display": f"+${expected_return:.2f}",
        "pnl_val": pnl_val,
        "pnl_display": pnl_display,
        "pnl_class": pnl_class,
        "time_display": time_display,
    }


def weather_summary() -> dict:
    trades = _load_trades()
    settled = [t for t in trades if _is_settled(t)]
    pending = [t for t in trades if not _is_settled(t)]
    wins = sum(1 for t in settled if _trade_result(t) == "WIN")
    total_pnl = sum(float(t["pnl"]) for t in settled if t.get("pnl") is not None)
    capital = _initial_capital()
    win_rate = (wins / len(settled) * 100) if settled else 0.0

    sane = [t for t in trades if float(t.get("edge", 0)) < 0.5]
    avg_edge = (
        sum(float(t.get("edge", 0)) for t in sane) / len(sane) * 100 if sane else 0.0
    )

    city_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "settled": 0, "pnl": 0.0})
    for t in settled:
        city = t.get("city_name") or t.get("city_key", "Unknown")
        city_stats[city]["settled"] += 1
        if _trade_result(t) == "WIN":
            city_stats[city]["wins"] += 1
        if t.get("pnl") is not None:
            city_stats[city]["pnl"] += float(t["pnl"])

    def _city_rank_key(item: tuple[str, dict]) -> tuple:
        _city, s = item
        wr = s["wins"] / s["settled"] if s["settled"] else 0.0
        return (-round(s["pnl"], 4), -wr, -s["settled"])

    ranked_cities = sorted(city_stats.items(), key=_city_rank_key)
    best_city = ranked_cities[0][0] if ranked_cities else "—"

    today_iso = datetime.now(HKT).date().isoformat()
    today_staked = sum(float(t.get("stake", 0)) for t in trades if t.get("date") == today_iso)
    today_trades = sum(1 for t in trades if t.get("date") == today_iso)
    daily_pnl = round(
        sum(
            float(t["pnl"])
            for t in settled
            if t.get("date") == today_iso and t.get("pnl") is not None
        ),
        2,
    )

    return {
        "available": TRADES_FILE.exists(),
        "mode": "PAPER",
        "initial_capital": capital,
        "balance": round(capital + total_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "daily_pnl_hkt": daily_pnl,
        "overall_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "avg_edge_pct": round(avg_edge, 1),
        "best_city": best_city,
        "pending_trades": len(pending),
        "settled_trades": len(settled),
        "total_trades": len(trades),
        "wins": wins,
        "losses": len(settled) - wins,
        "today_staked": round(today_staked, 2),
        "today_trades": today_trades,
        "city_stats": {
            k: {
                "settled": v["settled"],
                "wins": v["wins"],
                "win_rate": round(v["wins"] / v["settled"] * 100, 1) if v["settled"] else 0,
                "pnl": round(v["pnl"], 2),
            }
            for k, v in ranked_cities
        },
        "city_ranked": [
            {
                "city": k,
                "settled": v["settled"],
                "wins": v["wins"],
                "win_rate": round(v["wins"] / v["settled"] * 100, 1) if v["settled"] else 0,
                "pnl": round(v["pnl"], 2),
            }
            for k, v in ranked_cities
        ],
        "weather_bot_path": str(WEATHER_ROOT),
    }


def weather_trades(limit: int = 100) -> list[dict]:
    trades = _load_trades()
    enriched = [_enrich_trade(t) for t in reversed(trades)]
    return enriched[:limit]


def safe_weather_summary() -> dict:
    try:
        return weather_summary()
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "mode": "PAPER",
            "initial_capital": 0,
            "balance": 0,
            "total_pnl": 0,
            "daily_pnl_hkt": 0,
            "overall_pnl": 0,
            "win_rate": 0,
            "avg_edge_pct": 0,
            "best_city": "—",
            "pending_trades": 0,
            "settled_trades": 0,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "today_staked": 0,
            "today_trades": 0,
            "city_stats": {},
            "city_ranked": [],
            "weather_bot_path": str(WEATHER_ROOT),
        }


def safe_weather_trades(limit: int = 100) -> list[dict]:
    try:
        return weather_trades(limit)
    except Exception:
        return []
