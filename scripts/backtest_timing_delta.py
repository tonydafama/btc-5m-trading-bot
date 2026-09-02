#!/usr/bin/env python3
"""Backtest OLD strategy: timing × delta grid on paper + skipped session windows."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategy_rules import evaluate_snapshot, best_entry_checkpoint

MIN_MOVE = 12
MIN_DEPTH = 50
MAX_SPREAD = 0.06
OLD_MAX_ASK = 0.88
OLD_BET = 5.0

TIMING_GRID = [
    ("T60-22 (現況)", 60, 22),
    ("T60-20", 60, 20),
    ("T60-18", 60, 18),
    ("T60-15", 60, 15),
    ("T55-22", 55, 22),
    ("T55-18", 55, 18),
    ("T50-22", 50, 22),
]

DELTA_GRID = [0.025, 0.022, 0.020]


def win_pnl(stake: float, ask: float) -> float:
    return round(stake * (1 - ask) / ask, 2) if ask and ask > 0 else 0.0


def contract(side: str) -> str:
    return "UP" if side == "up" else "DOWN"


def passes(t_rem, delta_usd, delta_pct, depth, spread, t_max, t_min, min_delta):
    if t_rem > t_max or t_rem < t_min:
        return False
    if abs(delta_usd) < MIN_MOVE:
        return False
    if abs(delta_pct) < min_delta:
        return False
    if depth < MIN_DEPTH:
        return False
    if spread is not None and spread > MAX_SPREAD:
        return False
    return True


def pick_new_snap(checkpoints: list[dict], t_max: int, t_min: int, min_delta: float):
    """Match observer._pick_new_snap + timing_ok gate for OLD entry."""
    reeval = []
    for c in checkpoints:
        t = c.get("t_remaining", 0)
        du = c.get("delta_usd", 0)
        dp = c.get("delta_pct", 0)
        ev = evaluate_snapshot(t, du, dp, c.get("up_book", {}), c.get("down_book", {}))
        skip = [s for s in (ev.get("skip_reasons") or []) if not s.startswith("delta_")]
        if not (t_min <= t <= t_max):
            skip = list(skip) + [f"window_T{t:.0f}"]
        if abs(du) < MIN_MOVE:
            skip = list(skip) + [f"btc_move_lt_{MIN_MOVE}"]
        if abs(dp) < min_delta:
            skip = list(skip) + [f"delta_lt_{min_delta}"]
        ev["skip_reasons"] = skip or None
        ev["in_entry_window"] = t_min <= t <= t_max
        ev["would_enter"] = ev["in_entry_window"] and not skip
        reeval.append({**c, **ev})

    best = best_entry_checkpoint(reeval)
    snap = best
    if snap is None:
        in_window = [c for c in reeval if c.get("in_entry_window")]
        if not in_window:
            return None
        with_ask = [c for c in in_window if c.get("ask") is not None]
        snap = min(with_ask, key=lambda c: c.get("ask") or 999) if with_ask else in_window[0]

    skip = snap.get("skip_reasons") or []
    blockers = [s for s in skip if not s.startswith("ask_")]
    if blockers:
        return None
    return snap


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": None, "pnl": 0.0, "avg_pnl": None, "roi_pct": None}
    wins = sum(1 for t in trades if t["won"])
    pnl = round(sum(t["pnl"] for t in trades), 2)
    staked = OLD_BET * len(trades)
    return {
        "n": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "wr": round(wins / len(trades) * 100, 1),
        "pnl": pnl,
        "avg_pnl": round(pnl / len(trades), 2),
        "roi_pct": round(pnl / staked * 100, 2),
    }


def backtest_paper(paper: list[dict], t_max, t_min, min_delta) -> list[dict]:
    trades = []
    for t in paper:
        t_rem = t.get("t_remaining")
        du = abs(t.get("delta_usd") or 0)
        dp = abs(t.get("delta_pct") or 0)
        ask = t.get("entry_price")
        depth = t.get("ask_depth_usd", 999)
        spread = t.get("spread")
        side = t.get("side", "up" if (t.get("delta_pct") or 0) >= 0 else "down")
        if ask is None or ask <= 0 or ask > OLD_MAX_ASK:
            continue
        if not passes(t_rem, du, dp, depth, spread, t_max, t_min, min_delta):
            continue
        outcome = (t.get("outcome") or "").lower()
        res = "UP" if outcome == "up" else "DOWN"
        won = contract(side) == res
        pnl = win_pnl(OLD_BET, ask) if won else -OLD_BET
        trades.append({"slug": t.get("slug"), "ask": ask, "won": won, "pnl": pnl, "source": "paper"})
    return trades


def backtest_skipped(rows, win_by_slug, outcomes, t_max, t_min, min_delta) -> list[dict]:
    trades = []
    for r in rows:
        slug = r["slug"]
        w = win_by_slug.get(slug)
        outcome = outcomes.get(slug)
        if not w or outcome not in ("UP", "DOWN"):
            continue
        snap = pick_new_snap(w.get("checkpoints") or [], t_max, t_min, min_delta)
        if not snap:
            continue
        ask = snap.get("ask")
        if not ask or ask <= 0 or ask > OLD_MAX_ASK:
            continue
        side = snap.get("side", "up")
        won = contract(side) == outcome
        pnl = win_pnl(OLD_BET, ask) if won else -OLD_BET
        trades.append({"slug": slug, "ask": ask, "won": won, "pnl": pnl, "source": "skipped"})
    return trades


def main():
    paper = json.load(open(ROOT / "logs/paper_trades.json"))
    rows = json.load(open(ROOT / "logs/trade_log_full.json"))
    windows = json.load(open(ROOT / "logs/windows.json"))

    slug_set = {r["slug"] for r in rows}
    win_by_slug = {w["slug"]: w for w in windows if w.get("slug") in slug_set}
    outcomes = {}
    for r in rows:
        res = (r.get("resolved_outcome") or "").upper()
        if res in ("UP", "DOWN"):
            outcomes[r["slug"]] = res

    results = []
    for t_label, t_max, t_min in TIMING_GRID:
        for min_delta in DELTA_GRID:
            d_label = f"delta {min_delta:.3f}%"
            paper_tr = backtest_paper(paper, t_max, t_min, min_delta)
            skip_tr = backtest_skipped(rows, win_by_slug, outcomes, t_max, t_min, min_delta)
            combined = paper_tr + skip_tr
            key = f"{t_label} · {d_label}"
            results.append({
                "key": key,
                "timing": t_label,
                "t_max": t_max,
                "t_min": t_min,
                "min_delta": min_delta,
                "paper": summarize(paper_tr),
                "skipped": summarize(skip_tr),
                "combined": summarize(combined),
            })

    results.sort(key=lambda x: x["combined"]["pnl"], reverse=True)

    out = {
        "method": "OLD strategy · ask≤0.88 · $5 · move≥$12 · bot _pick_new_snap logic",
        "paper_n": len(paper),
        "skipped_windows": len(slug_set),
        "grid": results,
        "best": results[0] if results else None,
        "best_delta_022": max(
            (r for r in results if r["min_delta"] == 0.022),
            key=lambda x: x["combined"]["pnl"],
            default=None,
        ),
    }
    out_path = ROOT / "logs/backtest_timing_delta.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # human-readable report
    lines = [
        "=" * 80,
        "BACKTEST: timing × delta (OLD $5 · ask≤0.88 · move≥$12 · bot decision logic)",
        f"Paper: {len(paper)} trades | Skipped session: {len(slug_set)} windows",
        "=" * 80,
        "",
        "TOP 10 (combined PnL):",
        f"{'Rank':<5} {'Scenario':<32} {'Paper':>8} {'Skip':>8} {'Comb':>8} {'Comb_n':>7} {'WR':>7}",
        "-" * 80,
    ]
    for i, r in enumerate(results[:10], 1):
        c = r["combined"]
        lines.append(
            f"{i:<5} {r['key']:<32} ${r['paper']['pnl']:>+7.2f} ${r['skipped']['pnl']:>+7.2f} "
            f"${c['pnl']:>+7.2f} {c['n']:>7} {c['wr']:>6}%"
        )

    if out["best_delta_022"]:
        b = out["best_delta_022"]
        lines += [
            "",
            "BEST with delta 0.022%:",
            f"  {b['key']}",
            f"  Paper: {b['paper']['n']} trades ${b['paper']['pnl']:+.2f} WR {b['paper']['wr']}%",
            f"  Skipped: {b['skipped']['n']} trades ${b['skipped']['pnl']:+.2f} WR {b['skipped']['wr']}%",
            f"  Combined: {b['combined']['n']} trades ${b['combined']['pnl']:+.2f} WR {b['combined']['wr']}% ROI {b['combined']['roi_pct']}%",
        ]

    if out["best"]:
        b = out["best"]
        lines += [
            "",
            "OVERALL BEST:",
            f"  {b['key']}",
            f"  Combined: {b['combined']['n']} trades ${b['combined']['pnl']:+.2f} WR {b['combined']['wr']}%",
        ]

    report = "\n".join(lines)
    (ROOT / "logs/backtest_timing_delta_report.txt").write_text(report)
    print(report)
    print(f"\nSaved {out_path}")


if __name__ == "__main__":
    main()
