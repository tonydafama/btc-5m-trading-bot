#!/usr/bin/env python3
"""Hourly WR/PnL from live trade logs (ET + HKT). Writes logs/hourly_performance.json."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
OUT = ROOT / "logs" / "hourly_performance.json"

LOGS = {
    "btc": ROOT / "logs" / "trade_log_live.json",
    "eth": ROOT / "logs" / "trade_log_eth_live.json",
}


def parse_hkt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    s = str(ts).replace(" HKT", "").strip()
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=HKT)
    except ValueError:
        return None


def closed_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    rows = data if isinstance(data, list) else data.get("trades", [])
    return [
        r
        for r in rows
        if r.get("trade_result") in ("WIN", "LOSS") and float(r.get("stake") or 0) > 0
    ]


def bucket_hourly(rows: list[dict], tz: ZoneInfo, label: str) -> dict[str, dict]:
    buckets: dict[str, dict] = defaultdict(lambda: {"n": 0, "w": 0, "l": 0, "pnl": 0.0})
    for r in rows:
        dt = parse_hkt(r.get("timestamp_hkt") or r.get("settled_at_hkt"))
        if not dt:
            continue
        local = dt.astimezone(tz)
        key = f"{local.strftime('%a')} {local.hour:02d}:00"
        buckets[key]["n"] += 1
        buckets[key]["pnl"] += float(r.get("pnl") or 0)
        if r.get("trade_result") == "WIN":
            buckets[key]["w"] += 1
        else:
            buckets[key]["l"] += 1
    out = {}
    for k, v in buckets.items():
        n = v["n"]
        out[k] = {
            "trades": n,
            "wins": v["w"],
            "losses": v["l"],
            "win_rate_pct": round(100 * v["w"] / n, 1) if n else None,
            "pnl_usd": round(v["pnl"], 2),
            "avg_pnl_usd": round(v["pnl"] / n, 2) if n else None,
        }
    return out


def bucket_by_et_hour(rows: list[dict]) -> dict[int, dict]:
    """Aggregate by ET hour (0–23), all weekdays combined."""
    buckets: dict[int, dict] = defaultdict(lambda: {"n": 0, "w": 0, "l": 0, "pnl": 0.0, "hkt": None})
    for r in rows:
        dt = parse_hkt(r.get("timestamp_hkt") or r.get("settled_at_hkt"))
        if not dt:
            continue
        et = dt.astimezone(ET)
        h = et.hour
        buckets[h]["hkt"] = dt.astimezone(HKT).strftime("%H:00")
        buckets[h]["n"] += 1
        buckets[h]["pnl"] += float(r.get("pnl") or 0)
        if r.get("trade_result") == "WIN":
            buckets[h]["w"] += 1
        else:
            buckets[h]["l"] += 1
    out = {}
    for h in range(24):
        v = buckets.get(h) or {"n": 0, "w": 0, "l": 0, "pnl": 0.0, "hkt": None}
        n = v["n"]
        out[h] = {
            "hour_et": f"{h:02d}:00",
            "hour_hkt_sample": v.get("hkt"),
            "trades": n,
            "wins": v["w"],
            "losses": v["l"],
            "win_rate_pct": round(100 * v["w"] / n, 1) if n else None,
            "pnl_usd": round(v["pnl"], 2),
            "avg_pnl_usd": round(v["pnl"] / n, 2) if n else None,
        }
    return out


def top_hours(hourly: dict[int, dict], *, min_trades: int = 3) -> list[dict]:
    ranked = []
    for h, v in hourly.items():
        if v["trades"] < min_trades:
            continue
        ranked.append({"hour_et_int": h, **v})
    ranked.sort(key=lambda x: (x["pnl_usd"], x["win_rate_pct"] or 0), reverse=True)
    return ranked


def nyse_session_only(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        dt = parse_hkt(r.get("timestamp_hkt") or r.get("settled_at_hkt"))
        if not dt:
            continue
        et = dt.astimezone(ET)
        if et.weekday() >= 5:
            continue
        cur = et.hour * 60 + et.minute
        if 9 * 60 + 30 <= cur < 16 * 60:
            out.append(r)
    return out


def main() -> None:
    report: dict = {
        "generated_at_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
        "min_trades_for_ranking": 3,
        "assets": {},
        "combined_nyse_hours_et": {},
        "recommendations": {},
    }

    all_nyse: list[dict] = []
    for asset, path in LOGS.items():
        rows = closed_rows(path)
        nyse = nyse_session_only(rows)
        all_nyse.extend(nyse)
        hourly_et = bucket_by_et_hour(rows)
        hourly_nyse_et = bucket_by_et_hour(nyse)
        report["assets"][asset] = {
            "closed_total": len(rows),
            "closed_nyse_session": len(nyse),
            "by_et_hour_all": hourly_et,
            "by_et_hour_nyse_only": hourly_nyse_et,
            "top_et_hours_nyse": top_hours(hourly_nyse_et),
            "worst_et_hours_nyse": list(reversed(top_hours(hourly_nyse_et)[-5:])),
        }

    combined = bucket_by_et_hour(all_nyse)
    report["combined_nyse_hours_et"] = combined
    top = top_hours(combined, min_trades=3)
    bottom = list(reversed(top[-8:])) if top else []
    report["recommendations"] = {
        "best_et_hours_min3trades": top[:8],
        "worst_et_hours_min3trades": bottom,
        "suggested_core_window_et": _suggest_window(top),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print(f"Wrote {OUT}\n")
    for asset in ("btc", "eth"):
        a = report["assets"][asset]
        print(f"=== {asset.upper()} — NYSE session (Mon–Fri 9:30–16 ET) by hour ===")
        for h in range(9, 16):
            v = a["by_et_hour_nyse_only"].get(h, {})
            if not v.get("trades"):
                continue
            print(
                f"  {h:02d}:00 ET | {v['trades']:2d} trades | WR {v['win_rate_pct']:5.1f}% | "
                f"PnL ${v['pnl_usd']:+7.2f} | avg ${v['avg_pnl_usd']:+.2f}"
            )
        print("  Top hours:", ", ".join(
            f"{x['hour_et']} (${x['pnl_usd']:+.0f})" for x in a["top_et_hours_nyse"][:4]
        ) or "—")
        print()

    print("=== COMBINED BTC+ETH (NYSE hours only) — best hours (≥3 trades) ===")
    for x in top[:10]:
        print(
            f"  {x['hour_et']} ET | {x['trades']} trades | WR {x['win_rate_pct']}% | "
            f"PnL ${x['pnl_usd']:+.2f} | avg ${x['avg_pnl_usd']:+.2f}"
        )
    sug = report["recommendations"]["suggested_core_window_et"]
    if sug:
        print(f"\nSuggested core window: {sug['start_et']}–{sug['end_et']} ET ({sug['note']})")


def _suggest_window(top: list[dict]) -> dict | None:
    if not top:
        return None
    good = [x for x in top if x["pnl_usd"] > 0 and (x["win_rate_pct"] or 0) >= 75]
    if not good:
        good = top[:3]
    hours = sorted(x["hour_et_int"] for x in good)
    if not hours:
        return None
    best_start, best_len = hours[0], 1
    run_start, run_len = hours[0], 1
    for i in range(1, len(hours)):
        if hours[i] == hours[i - 1] + 1:
            run_len += 1
        else:
            run_start, run_len = hours[i], 1
        if run_len > best_len:
            best_start, best_len = run_start, run_len
    end_h = best_start + best_len
    return {
        "start_et": f"{best_start:02d}:00",
        "end_et": f"{end_h:02d}:00",
        "hours": best_len,
        "note": f"{best_len}h contiguous block from top-ranked NYSE hours (≥3 trades, positive PnL preferred)",
    }


if __name__ == "__main__":
    main()
