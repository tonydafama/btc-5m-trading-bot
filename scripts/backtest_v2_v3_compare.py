#!/usr/bin/env python3
"""Backtest V1 baseline vs V2 / V3 candidates on full historical tape."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from new_strategy import calc_loss_pnl, calc_win_pnl
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")

TAPE_PATHS = [
    ROOT / "logs" / "backtest_tape.jsonl",
    ROOT / "logs" / "backtest_archive" / "pre-1000-20260624" / "backtest_tape.jsonl",
    ROOT / "logs" / "backtest_archive" / "pre-1000-launch-20260624-121058" / "backtest_tape.jsonl",
]

STAKES = {
    "BTC": {"fav": 54.0, "dog": 90.0, "core_mult": 1.5},
    "ETH": {"fav": 48.0, "dog": 90.0, "core_mult": 1.5},
}


def parse_hkt(ts: str) -> datetime:
    return datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=HKT)


def et_hour(ts: str) -> int:
    return parse_hkt(ts).astimezone(ET).hour


def load_tape() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in TAPE_PATHS:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rid = row.get("id")
            if rid in seen:
                continue
            seen.add(rid)
            rows.append(row)
    rows.sort(key=lambda r: r.get("timestamp_hkt") or "")
    return rows


def stake_for(asset: str, tier: str, *, core: bool, mult: float = 1.0) -> float:
    s = STAKES[asset.upper()]
    base = s["fav"] if tier == "favorite" else s["dog"]
    out = base * (s["core_mult"] if core else 1.0)
    return round(out * mult, 2)


def pnl_normalized(row: dict[str, Any], stake: float) -> float:
    """Same win/loss outcome at standardized plan stake."""
    ask = float(row.get("entry_ask") or 0)
    if stake <= 0 or ask <= 0:
        return 0.0
    won = row.get("trade_result") == "WIN"
    return round(calc_win_pnl(stake, ask) if won else calc_loss_pnl(stake), 2)


def actual_pnl(row: dict[str, Any]) -> float:
    return round(float(row.get("pnl") or 0), 2)


def summarize(name: str, rows: list[dict[str, Any]], *, reasons: list[str]) -> dict[str, Any]:
    if not rows:
        return {"name": name, "trades": 0, "wins": 0, "losses": 0, "wr_pct": None, "pnl": 0.0, "reasons": reasons}
    wins = sum(1 for r in rows if r["trade_result"] == "WIN")
    pnl = round(sum(float(r["_sim_pnl"]) for r in rows), 2)
    by_seg: dict[str, dict[str, Any]] = defaultdict(lambda: {"n": 0, "w": 0, "pnl": 0.0})
    for r in rows:
        key = f"{r['asset']}|{r['period']}|{r['tier']}"
        by_seg[key]["n"] += 1
        by_seg[key]["w"] += 1 if r["trade_result"] == "WIN" else 0
        by_seg[key]["pnl"] = round(by_seg[key]["pnl"] + float(r["_sim_pnl"]), 2)
    return {
        "name": name,
        "trades": len(rows),
        "wins": wins,
        "losses": len(rows) - wins,
        "wr_pct": round(100 * wins / len(rows), 1),
        "pnl": pnl,
        "avg_pnl": round(pnl / len(rows), 2),
        "by_segment": dict(sorted(by_seg.items())),
        "reasons": reasons,
    }


def simulate(
    rows: list[dict[str, Any]],
    accept: Callable[[dict], bool],
    stake_fn: Callable[[dict], float] | None = None,
    *,
    mode: str = "normalized",
) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        if row.get("tier") not in ("favorite", "underdog"):
            continue
        if not accept(row):
            continue
        r = dict(row)
        if mode == "actual":
            r["_sim_stake"] = float(row.get("stake") or 0)
            r["_sim_pnl"] = actual_pnl(row)
        else:
            r["_sim_stake"] = stake_fn(row) if stake_fn else float(row.get("stake") or 0)
            r["_sim_pnl"] = pnl_normalized(row, r["_sim_stake"])
        out.append(r)
    return out


def v1_accept(row: dict) -> bool:
    """V1 baseline @ $1000 fixed: fav core-only, dog core+non-core, no wallet scaling."""
    tier = row["tier"]
    period = row["period"]
    if tier == "favorite" and period != "core":
        return False
    return True


def v1_stake(row: dict) -> float:
    return stake_for(row["asset"], row["tier"], core=row["period"] == "core")


def v2_accept(row: dict) -> bool:
    """V2: core-only everything; stricter dog ask band; no non-core exposure."""
    if row["period"] != "core":
        return False
    if row["tier"] == "underdog":
        ask = float(row.get("entry_ask") or 0)
        if ask < 0.18 or ask > 0.28:
            return False
    return True


def v2_stake(row: dict) -> float:
    return stake_for(row["asset"], row["tier"], core=True)


def v3_accept(row: dict) -> bool:
    """V3: 15m/1h style hour filter — BTC 12-14 ET fav, ETH 11-14 ET fav only."""
    if row["tier"] != "favorite":
        return False
    if row["period"] != "core":
        return False
    h = et_hour(row["timestamp_hkt"])
    asset = row["asset"]
    if asset == "BTC" and h not in (12, 13, 14):
        return False
    if asset == "ETH" and h not in (11, 12, 13, 14):
        return False
    ask = float(row.get("entry_ask") or 0)
    if ask > 0.83:
        return False
    return True


def v3_stake(row: dict) -> float:
    # Half core fav stakes — slower, cleaner challenger
    return stake_for(row["asset"], "favorite", core=True, mult=0.5)


def main() -> None:
    rows = load_tape()
    start = rows[0]["timestamp_hkt"][:10] if rows else "?"
    end = rows[-1]["timestamp_hkt"][:10] if rows else "?"

    v1_norm = simulate(rows, v1_accept, v1_stake, mode="normalized")
    v2_norm = simulate(rows, v2_accept, v2_stake, mode="normalized")
    v3_norm = simulate(rows, v3_accept, v3_stake, mode="normalized")
    v1_actual = simulate(rows, v1_accept, mode="actual")

    # Actual historical entered P&L (all tiers, actual stakes)
    actual_pnl = round(sum(float(r.get("pnl") or 0) for r in rows if r.get("tier") in ("favorite", "underdog")), 2)

    report = {
        "dataset": {
            "source": "backtest_tape.jsonl + archives",
            "unique_trades": len(rows),
            "date_range_hkt": f"{start} → {end}",
            "actual_all_entered_pnl": actual_pnl,
            "note": "420 closed trades Jun15–Jun26. Normalized = same outcomes at $1000 plan stakes.",
        },
        "strategies_normalized_1000": {
            "V1_baseline": summarize(
                "V1 Baseline @ $1000 plan stakes",
                v1_norm,
                reasons=[
                    "Fav core-only · Dog core+non-core · 固定 $1000 注碼",
                    "Normalized：同一批 win/loss，用 plan A 注碼重算",
                ],
            ),
            "V2_core_strict": summarize(
                "V2 Core-only + strict dog",
                v2_norm,
                reasons=[
                    "只做 core · Dog ask 0.18–0.28 · 砍掉 non-core",
                ],
            ),
            "V3_hour_fav": summarize(
                "V3 Hour-filter fav only",
                v3_norm,
                reasons=[
                    "BTC 12–14 ET · ETH 11–14 ET · fav only · 半注碼",
                ],
            ),
        },
        "v1_actual_historical": summarize(
            "V1 rules · actual historical stakes/PnL",
            v1_actual,
            reasons=["真實歷史成交注碼，不標準化"],
        ),
        "comparison_normalized": {
            "V1_pnl": summarize("", v1_norm, reasons=[])["pnl"],
            "V2_pnl": summarize("", v2_norm, reasons=[])["pnl"],
            "V3_pnl": summarize("", v3_norm, reasons=[])["pnl"],
            "V2_minus_V1": round(
                summarize("", v2_norm, reasons=[])["pnl"] - summarize("", v1_norm, reasons=[])["pnl"], 2
            ),
            "V3_minus_V1": round(
                summarize("", v3_norm, reasons=[])["pnl"] - summarize("", v1_norm, reasons=[])["pnl"], 2
            ),
        },
        "v2_removed_trades": summarize(
            "V1 took · V2 skipped (normalized PnL removed)",
            [r for r in v1_norm if r["id"] not in {x["id"] for x in v2_norm}],
            reasons=["V2 砍掉的交易，normalized 口徑"],
        ),
    }

    out = ROOT / "logs" / "backtest_v2_v3_compare.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
