#!/usr/bin/env python3
"""NEW strategy backtest — recent session only, timing × delta grid."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategy_rules import evaluate_snapshot, best_entry_checkpoint
from new_strategy import evaluate_ask_tier, calc_win_pnl, calc_loss_pnl

MIN_MOVE = 12
MIN_DEPTH = 50
MAX_SPREAD = 0.06
START_BANKROLL = 102.03

TIMING_GRID = [
    ("T60-22 (現況)", 60, 22),
    ("T60-20", 60, 20),
    ("T60-18", 60, 18),
    ("T60-15", 60, 15),
    ("T55-22", 55, 22),
    ("T55-18", 55, 18),
    ("T50-22", 50, 22),
    ("T65-22", 65, 22),
    ("T70-22", 70, 22),
]

DELTA_GRID = [0.025, 0.022, 0.020]


def contract(side: str) -> str:
    return "UP" if side == "up" else "DOWN"


def pick_snap(checkpoints: list[dict], t_max: int, t_min: int, min_delta: float):
    reeval = []
    for c in checkpoints:
        t = c.get("t_remaining", 0)
        du = c.get("delta_usd", 0)
        dp = c.get("delta_pct", 0)
        ev = evaluate_snapshot(t, du, dp, c.get("up_book", {}), c.get("down_book", {}))
        skip = [s for s in (ev.get("skip_reasons") or []) if not s.startswith("delta_")]
        if not (t_min <= t <= t_max):
            skip = list(skip) + ["window"]
        if abs(du) < MIN_MOVE:
            skip = list(skip) + ["move"]
        if abs(dp) < min_delta:
            skip = list(skip) + ["delta"]
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

    if snap.get("skip_reasons"):
        return None
    return snap


def run_scenario(rows, win_by_slug, outcomes, t_max, t_min, min_delta):
    bankroll = START_BANKROLL
    trades = []
    skip_reasons = {"timing": 0, "ask": 0, "no_snap": 0}

    for r in sorted(rows, key=lambda x: x.get("timestamp_hkt", "")):
        slug = r["slug"]
        w = win_by_slug.get(slug)
        outcome = outcomes.get(slug)
        if not w or outcome not in ("UP", "DOWN"):
            continue

        snap = pick_snap(w.get("checkpoints") or [], t_max, t_min, min_delta)
        if not snap:
            skip_reasons["timing"] += 1
            continue

        ask = snap.get("ask")
        if not ask or ask <= 0:
            skip_reasons["no_snap"] += 1
            continue

        tier = evaluate_ask_tier(float(ask), bankroll)
        if not tier["entered"]:
            skip_reasons["ask"] += 1
            continue

        side = snap.get("side", "up")
        stake = float(tier["stake"])
        won = contract(side) == outcome
        pnl = calc_win_pnl(stake, ask) if won else calc_loss_pnl(stake)
        bankroll = round(bankroll + pnl, 2)
        trades.append({
            "slug": slug,
            "ask": ask,
            "stake": stake,
            "side": side,
            "won": won,
            "pnl": pnl,
            "bankroll_after": bankroll,
            "t": snap.get("t_remaining"),
            "move": abs(snap.get("delta_usd") or 0),
            "delta": abs(snap.get("delta_pct") or 0),
        })

    wins = sum(1 for t in trades if t["won"])
    total_pnl = round(bankroll - START_BANKROLL, 2)
    return {
        "trades": trades,
        "n": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "wr": round(wins / len(trades) * 100, 1) if trades else None,
        "pnl": total_pnl,
        "final_bankroll": bankroll,
        "skip_timing": skip_reasons["timing"],
        "skip_ask": skip_reasons["ask"],
        "windows": len(rows),
    }


def main():
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
            r = run_scenario(rows, win_by_slug, outcomes, t_max, t_min, min_delta)
            results.append({
                "key": f"{t_label} · delta {min_delta:.3f}%",
                "timing": t_label,
                "t_max": t_max,
                "t_min": t_min,
                "min_delta": min_delta,
                **{k: r[k] for k in ("n", "wins", "losses", "wr", "pnl", "final_bankroll", "skip_timing", "skip_ask", "windows")},
                "trades": r["trades"],
            })

    results.sort(key=lambda x: x["pnl"], reverse=True)

    # actual NEW performance
    actual_entered = [r for r in rows if r.get("entered")]
    actual_pnl = sum(float(r.get("pnl") or 0) for r in rows if r.get("trade_result") in ("WIN", "LOSS"))

    out = {
        "method": "NEW strategy only · recent session · dynamic stake · ask≤0.60",
        "session_windows": len(rows),
        "resolved_windows": len(outcomes),
        "actual_new_entered": len(actual_entered),
        "actual_new_pnl": actual_pnl,
        "start_bankroll": START_BANKROLL,
        "grid": [{k: v for k, v in r.items() if k != "trades"} for r in results],
        "best": results[0] if results else None,
        "best_delta_022": max((r for r in results if r["min_delta"] == 0.022), key=lambda x: x["pnl"], default=None),
    }
    (ROOT / "logs/backtest_new_session_timing.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        "=" * 78,
        "NEW STRATEGY BACKTEST — recent session ONLY (no old 169 paper)",
        f"Windows: {len(rows)} | Resolved: {len(outcomes)} | Actual NEW entered: {len(actual_entered)} | Actual PnL: ${actual_pnl:+.2f}",
        "Rules: ask≤0.60 · dynamic stake · move≥$12 · bot decision logic",
        "=" * 78,
        "",
        f"{'Scenario':<30} {'進場':>4} {'略過timing':>10} {'略過ask':>8} {'勝率':>7} {'PnL':>9}",
        "-" * 78,
    ]
    for r in results[:15]:
        wr = f"{r['wr']}%" if r["wr"] is not None else "—"
        lines.append(
            f"{r['key']:<30} {r['n']:>4} {r['skip_timing']:>10} {r['skip_ask']:>8} {wr:>7} ${r['pnl']:>+8.2f}"
        )

    lines += [
        "",
        "NOTE: Sample is small — most scenarios have 0–3 NEW entries. Treat as directional only.",
    ]
    if out["best"]:
        b = out["best"]
        lines += [
            "",
            f"BEST: {b['key']}",
            f"  {b['n']} trades · {b['wins']}W/{b['losses']}L · PnL ${b['pnl']:+.2f} · bankroll ${b['final_bankroll']:.2f}",
        ]
        for t in b.get("trades", []):
            lines.append(
                f"    {t['slug'][-22:]} ask={t['ask']:.2f} stake=${t['stake']:.2f} "
                f"{'WIN' if t['won'] else 'LOSS'} ${t['pnl']:+.2f}"
            )

    report = "\n".join(lines)
    (ROOT / "logs/backtest_new_session_timing_report.txt").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
