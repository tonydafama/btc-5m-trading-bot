"""Shared ops + live execution metrics for dashboard and daily report."""

from __future__ import annotations

import json
import statistics
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot_common import (
    ETH_LIVE_TRADE_LOG_FILE,
    LIVE_TRADE_LOG_FILE,
    LOG_DIR,
)

ROOT = Path("/root/btc-5m-bot")
HKT = ZoneInfo("Asia/Hong_Kong")


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _parse_hkt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=HKT)
    except ValueError:
        return None


def _rows_in_lookback(rows: list[dict], days: int) -> list[dict]:
    if days <= 0:
        return rows
    cutoff = datetime.now(HKT) - timedelta(days=days)
    out: list[dict] = []
    for row in rows:
        dt = _parse_hkt(row.get("timestamp_hkt"))
        if dt is None or dt >= cutoff:
            out.append(row)
    return out


def _slippage(row: dict) -> float | None:
    decision = row.get("decision_ask")
    fill = row.get("fill_price")
    if fill is None and isinstance(row.get("live_order"), dict):
        lo = row["live_order"]
        if lo.get("live_ask_at_fill") is not None:
            fill = lo["live_ask_at_fill"]
    if decision is None or fill is None:
        return None
    return round(float(fill) - float(decision), 4)


def _execution_from_rows(rows: list[dict], *, bot: str) -> dict[str, Any]:
    fills = [
        r
        for r in rows
        if r.get("entered")
        and (
            r.get("fill_price") is not None
            or r.get("decision_ask") is not None
            or r.get("execution_path")
            or r.get("event") == "FILL"
        )
    ]
    slippages = [s for s in (_slippage(r) for r in fills) if s is not None]
    fok_fills = [r for r in fills if str(r.get("execution_path", "")).upper().startswith("FOK")]

    out: dict[str, Any] = {
        "bot": bot,
        "live_fills": len(fills),
        "fok_fills": len(fok_fills),
        "fok_success_rate": round(100 * len(fok_fills) / len(fills), 1) if fills else None,
        "avg_slippage": round(statistics.mean(slippages), 4) if slippages else None,
        "median_slippage": round(statistics.median(slippages), 4) if slippages else None,
        "slippage_samples": len(slippages),
        "note": "FOK rejections may not appear in trade_log; rate is fills with FOK path / all fills.",
    }
    return out


def execution_metrics(*, days: int = 7) -> dict[str, Any]:
    btc_rows = _rows_in_lookback(_read_json(LIVE_TRADE_LOG_FILE, []), days)
    eth_rows = _rows_in_lookback(_read_json(ETH_LIVE_TRADE_LOG_FILE, []), days)

    return {
        "generated_at_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S"),
        "lookback_days": days,
        "btc_live": _execution_from_rows(btc_rows, bot="btc"),
        "eth_live": _execution_from_rows(eth_rows, bot="eth"),
    }


def _process_running(pattern: str) -> bool:
    try:
        proc = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return bool(proc.stdout.strip())
    except Exception:
        return False


def _last_trade_time(rows: list[dict]) -> str | None:
    times = [r.get("timestamp_hkt") for r in rows if r.get("timestamp_hkt")]
    return max(times) if times else None


def _top_skips(counts: dict, n: int = 3) -> list[dict]:
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(counts.items(), key=lambda item: -item[1])[:n]
    ]


def _bot_snapshot(name: str, summary: dict[str, Any]) -> dict[str, Any]:
    p2 = summary.get("package2") or summary
    obs = summary.get("observation") or {}
    return {
        "bot": name,
        "mode": summary.get("mode") or p2.get("mode") or "—",
        "daily_pnl": p2.get("daily_pnl_hkt", summary.get("daily_pnl_hkt")),
        "total_pnl": p2.get("total_pnl", summary.get("total_pnl")),
        "trades_entered": p2.get("trades_entered", summary.get("trades_entered")),
        "trades_skipped": p2.get("trades_skipped", summary.get("trades_skipped")),
        "win_rate": p2.get("win_rate", summary.get("win_rate")),
        "windows_judged": obs.get("windows_judged"),
    }


def build_daily_ops_report(*, days: int = 1) -> dict[str, Any]:
    import sys

    sys.path.insert(0, str(ROOT))

    sys.path.insert(0, str(ROOT / "dashboard"))
    from eth_paper_engine import eth_summary
    from paper_engine import new_strategy_summary
    from weather_service import safe_weather_summary

    btc = new_strategy_summary(live_wallet=False)
    eth = eth_summary()
    weather = safe_weather_summary()
    from alpha_max import alpha_max_summary

    alpha = alpha_max_summary()
    exec_m = execution_metrics(days=max(days, 7))

    btc_rows = _read_json(LIVE_TRADE_LOG_FILE, [])
    eth_rows = _read_json(ETH_LIVE_TRADE_LOG_FILE, [])

    alerts: list[str] = []
    if not _process_running("observer.py"):
        alerts.append("BTC observer not running")
    if not _process_running("eth_observer.py"):
        alerts.append("ETH observer not running")

    fav_outside_us = [
        r
        for r in btc_rows[-200:]
        if r.get("entered")
        and float(r.get("entry_ask") or 0) >= 0.70
        and (r.get("trading_session") or {}).get("in_us_regular_session") is False
        and (r.get("trading_session") or {}).get("extended_dog_only") is True
    ]
    if fav_outside_us:
        alerts.append(f"BTC fav entry outside US session: {len(fav_outside_us)} recent row(s)")

    report = {
        "generated_at_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S"),
        "report_date_hkt": datetime.now(HKT).strftime("%Y-%m-%d"),
        "bots": {
            "btc": _bot_snapshot("btc", btc),
            "eth": _bot_snapshot("eth", eth),
            "weather": {
                "bot": "weather",
                "mode": "paper",
                "daily_pnl": weather.get("daily_pnl_hkt"),
                "total_pnl": weather.get("total_pnl"),
                "settled": weather.get("settled"),
                "win_rate": weather.get("win_rate"),
            },
            "alpha_max": {
                "bot": "alpha_max",
                "mode": alpha.get("mode"),
                "daily_pnl": alpha.get("daily_pnl_hkt"),
                "total_pnl": alpha.get("total_pnl"),
                "trades_closed": alpha.get("trades_closed"),
                "win_rate": alpha.get("win_rate"),
                "compare_v1_pnl": (alpha.get("v1_since_start") or {}).get("total_pnl"),
            },
        },
        "processes": {
            "btc_observer": _process_running("observer.py"),
            "eth_observer": _process_running("eth_observer.py"),
        },
        "execution": exec_m,
        "alerts": alerts,
        "last_trade_hkt": {
            "btc": _last_trade_time(btc_rows),
            "eth": _last_trade_time(eth_rows),
        },
    }
    return report


def format_daily_ops_text(report: dict[str, Any]) -> str:
    lines = [
        f"📊 Ops Report · {report.get('report_date_hkt')} · {report.get('generated_at_hkt')} HKT",
        "",
    ]
    for key, bot in (report.get("bots") or {}).items():
        daily = bot.get("daily_pnl")
        total = bot.get("total_pnl")
        daily_s = f"{daily:+.2f}" if isinstance(daily, (int, float)) else "—"
        total_s = f"{total:+.2f}" if isinstance(total, (int, float)) else "—"
        lines.append(f"{key.upper()}: daily ${daily_s} · total ${total_s}")
        if bot.get("top_skips"):
            skips = " · ".join(f"{s['reason']} ({s['count']})" for s in bot["top_skips"][:2])
            lines.append(f"  skip: {skips}")

    proc = report.get("processes") or {}
    proc_bits = [f"{k}={'✓' if v else '✗'}" for k, v in proc.items()]
    lines.extend(["", "Processes: " + " · ".join(proc_bits)])

    ex = report.get("execution") or {}
    for bot_key in ("btc_live", "eth_live"):
        b = ex.get(bot_key) or {}
        if not b.get("live_fills"):
            continue
        slip = b.get("avg_slippage")
        slip_s = f"{slip:+.4f}" if slip is not None else "—"
        line = f"Exec {bot_key}: fills={b.get('live_fills')} FOK={b.get('fok_fills')} avg_slip={slip_s}"
        if b.get("median_hedge_latency_sec") is not None:
            line += f" hedge_med={b['median_hedge_latency_sec']}s"
        lines.append(line)

    alerts = report.get("alerts") or []
    if alerts:
        lines.extend(["", "⚠️ " + " · ".join(alerts)])
    return "\n".join(lines)


def write_daily_ops_report(*, days: int = 1) -> Path:
    report = build_daily_ops_report(days=days)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_json = LOG_DIR / f"daily_ops_{report['report_date_hkt']}.json"
    out_txt = LOG_DIR / f"daily_ops_{report['report_date_hkt']}.txt"
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    out_txt.write_text(format_daily_ops_text(report) + "\n")
    return out_json
