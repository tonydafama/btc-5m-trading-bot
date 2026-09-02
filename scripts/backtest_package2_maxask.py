#!/usr/bin/env python3
"""Package 2+ backtest — max_ask_favorite × entry timing grid with marginal-trade analysis."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from new_strategy import calc_loss_pnl, calc_win_pnl
from package2_strategy import DEFAULTS, evaluate_package2
from strategy_rules import evaluate_snapshot, best_entry_checkpoint

START_BANKROLL = 102.29
MIN_MOVE = 12.0
MIN_DEPTH = 50.0
MAX_SPREAD = 0.06
MIN_DELTA = 0.025

FAV_CAP_GRID = [0.88, 0.90, 0.91, 0.92, 0.93]
TIMING_GRID = [
    ("T60-22", 60, 22),
    ("T75-22", 75, 22),
    ("T70-22", 70, 22),
    ("T55-22", 55, 22),
]


def contract(side: str) -> str:
    return "UP" if side == "up" else "DOWN"


def reeval_checkpoints(checkpoints: list[dict], t_max: int, t_min: int, min_delta: float) -> list[dict]:
    out = []
    for c in checkpoints:
        t = float(c.get("t_remaining") or 0)
        du = float(c.get("delta_usd") or 0)
        dp = float(c.get("delta_pct") or 0)
        ev = evaluate_snapshot(t, du, dp, c.get("up_book") or {}, c.get("down_book") or {})
        skip = [s for s in (ev.get("skip_reasons") or []) if not s.startswith("delta_")]
        if not (t_min <= t <= t_max):
            skip = list(skip) + [f"window_T{t:.0f}"]
        if abs(du) < MIN_MOVE:
            skip = list(skip) + [f"btc_move_lt_{MIN_MOVE:.0f}"]
        if abs(dp) < min_delta:
            skip = list(skip) + [f"delta_lt_{min_delta}"]
        ev["skip_reasons"] = skip or None
        ev["in_entry_window"] = t_min <= t <= t_max
        ev["would_enter"] = ev["in_entry_window"] and not skip
        merged = {**c, **ev}
        out.append(merged)
    return out


def pick_snap(checkpoints: list[dict], t_max: int, t_min: int, min_delta: float) -> Optional[dict]:
    reeval = reeval_checkpoints(checkpoints, t_max, t_min, min_delta)
    best = best_entry_checkpoint(reeval)
    snap = best
    if snap is None:
        in_window = [c for c in reeval if c.get("in_entry_window")]
        if not in_window:
            return None
        with_ask = [c for c in in_window if c.get("ask") is not None]
        snap = min(with_ask, key=lambda c: c.get("ask") or 999) if with_ask else in_window[0]
    blockers = [s for s in (snap.get("skip_reasons") or []) if not s.startswith("ask_")]
    if blockers:
        return None
    return snap


def evaluate_p2(ask: float, *, max_fav: float, snapshot: Optional[dict] = None) -> dict[str, Any]:
    """Package 2+ with overridden favorite cap (no underdog pause in backtest)."""
    cfg = dict(DEFAULTS)
    cfg["max_ask_favorite"] = max_fav
    fav = float(cfg["favorite_ask"])
    mid_lo = float(cfg["mid_low_ask"])
    max_ask = float(cfg["max_ask"])
    min_ask = float(cfg["min_ask"])

    if ask <= 0:
        return {"entered": False, "tier": "skip", "stake": 0.0, "strategy_reason": "no ask"}
    if ask < min_ask:
        return {"entered": False, "tier": "skip", "stake": 0.0, "strategy_reason": "min ask"}

    if ask >= fav:
        tier = "favorite"
        cap = max_fav
        stake = float(cfg["stake_favorite"])
    elif ask > mid_lo:
        tier = "mid"
        cap = max_ask
        stake = float(cfg["stake_mid"])
    else:
        tier = "underdog"
        cap = float(cfg.get("max_ask_underdog", max_ask))
        stake = float(cfg["stake_underdog"])
        if snapshot:
            depth = float(snapshot.get("ask_depth_usd") or 0)
            dp = abs(float(snapshot.get("delta_pct") or 0))
            du = abs(float(snapshot.get("delta_usd") or 0))
            t_rem = float(snapshot.get("t_remaining") or 0)
            if depth < float(cfg["underdog_min_depth"]):
                return {"entered": False, "tier": "skip", "stake": 0.0, "strategy_reason": "underdog depth"}
            if dp < float(cfg["underdog_min_delta_pct"]) and du < float(cfg["underdog_min_move_usd"]):
                return {"entered": False, "tier": "skip", "stake": 0.0, "strategy_reason": "underdog momentum"}
            if not (float(cfg["underdog_t_min"]) <= t_rem <= float(cfg["underdog_t_max"])):
                return {"entered": False, "tier": "skip", "stake": 0.0, "strategy_reason": "underdog timing"}

    if ask > cap:
        return {"entered": False, "tier": "skip", "stake": 0.0, "strategy_reason": f"ask>{cap:.2f}"}

    return {"entered": True, "tier": tier, "stake": stake, "strategy_reason": "enter"}


def max_drawdown(trades: list[dict]) -> float:
    peak = START_BANKROLL
    dd = 0.0
    for t in trades:
        br = t["bankroll_after"]
        peak = max(peak, br)
        dd = max(dd, peak - br)
    return round(dd, 2)


def run_backtest(
    slugs: list[str],
    win_by_slug: dict,
    outcomes: dict,
    *,
    t_max: int,
    t_min: int,
    max_fav: float,
    min_delta: float = MIN_DELTA,
) -> dict[str, Any]:
    bankroll = START_BANKROLL
    trades: list[dict] = []
    skips = {"timing": 0, "ask": 0, "no_outcome": 0}
    by_tier: dict[str, dict] = {}
    marginal: list[dict] = []  # ask > 0.88 only possible with max_fav > 0.88

    for slug in slugs:
        outcome = outcomes.get(slug)
        if outcome not in ("UP", "DOWN"):
            skips["no_outcome"] += 1
            continue
        w = win_by_slug.get(slug)
        if not w:
            continue
        snap = pick_snap(w.get("checkpoints") or [], t_max, t_min, min_delta)
        if not snap:
            skips["timing"] += 1
            continue
        ask = float(snap.get("ask") or 0)
        if ask <= 0:
            skips["ask"] += 1
            continue

        tier = evaluate_p2(ask, max_fav=max_fav, snapshot=snap)
        if not tier.get("entered"):
            skips["ask"] += 1
            continue

        side = snap.get("side", "up")
        stake = float(tier["stake"])
        won = contract(side) == outcome
        pnl = calc_win_pnl(stake, ask) if won else calc_loss_pnl(stake)
        bankroll = round(bankroll + pnl, 2)
        tname = tier["tier"]
        rec = {
            "slug": slug,
            "ask": ask,
            "tier": tname,
            "stake": stake,
            "won": won,
            "pnl": pnl,
            "bankroll_after": bankroll,
            "side": side,
            "outcome": outcome,
            "t": snap.get("t_remaining"),
            "marginal": ask > 0.88,
        }
        trades.append(rec)
        if ask > 0.88:
            marginal.append(rec)
        bt = by_tier.setdefault(tname, {"n": 0, "w": 0, "pnl": 0.0, "asks": []})
        bt["n"] += 1
        bt["w"] += int(won)
        bt["pnl"] = round(bt["pnl"] + pnl, 2)
        bt["asks"].append(ask)

    wins = sum(1 for t in trades if t["won"])
    total_pnl = round(bankroll - START_BANKROLL, 2)
    marg_pnl = round(sum(t["pnl"] for t in marginal), 2)
    marg_w = sum(1 for t in marginal if t["won"])
    return {
        "trades": trades,
        "n": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "wr": round(wins / len(trades) * 100, 1) if trades else None,
        "pnl": total_pnl,
        "final_bankroll": bankroll,
        "ev_per_trade": round(total_pnl / len(trades), 2) if trades else None,
        "max_dd": max_drawdown(trades),
        "skips": skips,
        "by_tier": by_tier,
        "marginal": {
            "n": len(marginal),
            "wins": marg_w,
            "losses": len(marginal) - marg_w,
            "wr": round(marg_w / len(marginal) * 100, 1) if marginal else None,
            "pnl": marg_pnl,
            "ev": round(marg_pnl / len(marginal), 2) if marginal else None,
        },
    }


def load_dataset() -> tuple[list[str], dict, dict, dict]:
    paper = json.loads((ROOT / "logs/trade_log_paper.json").read_text())
    full = json.loads((ROOT / "logs/trade_log_full.json").read_text())
    windows = json.loads((ROOT / "logs/windows.json").read_text())
    win_by_slug = {w["slug"]: w for w in windows}
    outcomes: dict[str, str] = {}
    ts_by_slug: dict[str, int] = {}
    for r in paper + full:
        res = (r.get("resolved_outcome") or "").upper()
        if res in ("UP", "DOWN"):
            outcomes[r["slug"]] = res
        slug = r["slug"]
        if slug in win_by_slug:
            ts_by_slug[slug] = int(win_by_slug[slug].get("window_start_ts") or 0)
        elif r.get("timestamp_hkt"):
            ts_by_slug[slug] = ts_by_slug.get(slug, 0)

    slugs = sorted(outcomes.keys(), key=lambda s: ts_by_slug.get(s, 0))
    return slugs, win_by_slug, outcomes, ts_by_slug


def partition_slugs(slugs: list[str], ts_by_slug: dict, ratio: float = 0.65) -> tuple[list[str], list[str]]:
    n = max(1, int(len(slugs) * ratio))
    train = [s for s in slugs if s in ts_by_slug][:n]
    test = [s for s in slugs if s not in set(train)]
    if not test:
        test = slugs[n // 2 :]
        train = slugs[: n // 2]
    return train, test


def main() -> None:
    slugs, win_by_slug, outcomes, ts_by_slug = load_dataset()
    train, test = partition_slugs(slugs, ts_by_slug)
    with_cp = [s for s in slugs if s in win_by_slug]

    grid: list[dict] = []
    for t_label, t_max, t_min in TIMING_GRID:
        for max_fav in FAV_CAP_GRID:
            for split_name, split_slugs in [("full", with_cp), ("train", train), ("test", test)]:
                r = run_backtest(split_slugs, win_by_slug, outcomes, t_max=t_max, t_min=t_min, max_fav=max_fav)
                grid.append({
                    "key": f"{t_label} · fav≤{max_fav:.2f} · {split_name}",
                    "timing": t_label,
                    "t_max": t_max,
                    "t_min": t_min,
                    "max_fav": max_fav,
                    "split": split_name,
                    "windows": len(split_slugs),
                    **{k: r[k] for k in (
                        "n", "wins", "losses", "wr", "pnl", "final_bankroll",
                        "ev_per_trade", "max_dd", "skips", "by_tier", "marginal",
                    )},
                })

    # Compare cap 0.88 vs 0.93 at same timing — incremental marginal value
    comparisons = []
    for t_label, t_max, t_min in TIMING_GRID:
        base = next(g for g in grid if g["split"] == "full" and g["timing"] == t_label and g["max_fav"] == 0.88)
        hi = next(g for g in grid if g["split"] == "full" and g["timing"] == t_label and g["max_fav"] == 0.93)
        comparisons.append({
            "timing": t_label,
            "pnl_088": base["pnl"],
            "pnl_093": hi["pnl"],
            "delta_pnl": round(hi["pnl"] - base["pnl"], 2),
            "trades_088": base["n"],
            "trades_093": hi["n"],
            "extra_trades": hi["n"] - base["n"],
            "marginal_pnl_at_093": hi["marginal"]["pnl"],
            "marginal_n": hi["marginal"]["n"],
            "marginal_wr": hi["marginal"]["wr"],
            "marginal_ev": hi["marginal"]["ev"],
        })

    # Ask-band analysis (full dataset, T60, cap 0.93) — quality by price bucket
    full_093_t60 = run_backtest(with_cp, win_by_slug, outcomes, t_max=60, t_min=22, max_fav=0.93)
    bands = [
        ("0.70–0.80", 0.70, 0.80),
        ("0.80–0.88", 0.80, 0.88),
        ("0.88–0.90", 0.88, 0.90),
        ("0.90–0.92", 0.90, 0.92),
        ("0.92–0.93", 0.92, 0.93),
        (">0.93", 0.93, 1.01),
    ]
    band_stats = []
    for label, lo, hi in bands:
        subset = [t for t in full_093_t60["trades"] if lo <= t["ask"] < hi]
        if not subset:
            band_stats.append({"band": label, "n": 0})
            continue
        w = sum(1 for t in subset if t["won"])
        pnl = round(sum(t["pnl"] for t in subset), 2)
        band_stats.append({
            "band": label,
            "n": len(subset),
            "wins": w,
            "wr": round(w / len(subset) * 100, 1),
            "pnl": pnl,
            "ev": round(pnl / len(subset), 2),
        })

    full_sorted = sorted(
        [g for g in grid if g["split"] == "full"],
        key=lambda x: x["pnl"],
        reverse=True,
    )
    test_sorted = sorted(
        [g for g in grid if g["split"] == "test"],
        key=lambda x: x["pnl"],
        reverse=True,
    )

    recommendation = build_recommendation(full_sorted, test_sorted, comparisons, band_stats)

    out = {
        "method": "Package 2+ · underdog filters · bot pick_snap logic",
        "start_bankroll": START_BANKROLL,
        "dataset": {
            "resolved_slugs": len(slugs),
            "with_checkpoints": len(with_cp),
            "train": len(train),
            "test": len(test),
        },
        "grid_full_top10": [{k: v for k, v in g.items() if k != "trades"} for g in full_sorted[:10]],
        "grid_test_top10": [{k: v for k, v in g.items() if k != "trades"} for g in test_sorted[:10]],
        "cap_comparison": comparisons,
        "ask_band_quality_T60_cap093": band_stats,
        "recommendation": recommendation,
    }
    out_path = ROOT / "logs/backtest_package2_maxask.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "=" * 88,
        "PACKAGE 2+ BACKTEST — max_ask_favorite × timing (480 windows w/ checkpoints)",
        f"Start bankroll ${START_BANKROLL} · train {len(train)} / test {len(test)} windows",
        "=" * 88,
        "",
        "TOP 8 FULL DATASET (by PnL):",
        f"{'Scenario':<28} {'Trades':>6} {'WR':>7} {'PnL':>9} {'EV/tr':>7} {'MaxDD':>7} {'Marg':>5} {'MargPnL':>8}",
        "-" * 88,
    ]
    for g in full_sorted[:8]:
        m = g["marginal"]
        wr = f"{g['wr']}%" if g["wr"] is not None else "—"
        lines.append(
            f"{g['key']:<28} {g['n']:>6} {wr:>7} ${g['pnl']:>+8.2f} ${g['ev_per_trade'] or 0:>+6.2f} "
            f"${g['max_dd']:>6.2f} {m['n']:>5} ${m['pnl']:>+7.2f}"
        )

    lines += ["", "0.88 vs 0.93 (marginal trades = ask > 0.88 only):", "-" * 88]
    for c in comparisons:
        lines.append(
            f"{c['timing']:<10} PnL ${c['pnl_088']:+.2f}→${c['pnl_093']:+.2f} (Δ${c['delta_pnl']:+.2f}) · "
            f"+{c['extra_trades']} trades · marginal {c['marginal_n']}笔 WR {c['marginal_wr']}% "
            f"EV ${c['marginal_ev'] or 0:+.2f}/tr"
        )

    lines += ["", "ASK BAND QUALITY (T60, cap 0.93):", "-" * 88]
    for b in band_stats:
        if b["n"] == 0:
            lines.append(f"  {b['band']:<12} — no trades")
        else:
            lines.append(
                f"  {b['band']:<12} {b['n']:>3} trades · WR {b['wr']:>5}% · PnL ${b['pnl']:>+7.2f} · EV ${b['ev']:>+5.2f}/tr"
            )

    lines += ["", "TEST SET TOP 5 (out-of-sample):", "-" * 88]
    for g in test_sorted[:5]:
        lines.append(f"  {g['key']:<28} {g['n']:>3} tr · WR {g['wr']}% · PnL ${g['pnl']:+.2f}")

    lines += ["", "RECOMMENDATION:", recommendation["summary"], ""]
    for bullet in recommendation["bullets"]:
        lines.append(f"  • {bullet}")
    lines += ["", f"DECISION: {recommendation['decision']}"]

    report = "\n".join(lines)
    (ROOT / "logs/backtest_package2_maxask_report.txt").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved {out_path}")


def build_recommendation(full_sorted, test_sorted, comparisons, band_stats) -> dict:
    best_full = full_sorted[0] if full_sorted else {}
    # Find best robust: good test set performance with cap 0.88 or 0.90
    test_088_t75 = next((g for g in test_sorted if "T75" in g["timing"] and g["max_fav"] == 0.88), None)
    test_088_t60 = next((g for g in test_sorted if "T60" in g["timing"] and g["max_fav"] == 0.88), None)
    test_090_t75 = next((g for g in test_sorted if "T75" in g["timing"] and g["max_fav"] == 0.90), None)

    marg_bands = {b["band"]: b for b in band_stats if b.get("n", 0) > 0}
    high_ask_pnl = sum(
        b.get("pnl", 0) for k, b in marg_bands.items() if k in ("0.88–0.90", "0.90–0.92", "0.92–0.93")
    )
    high_ask_n = sum(
        b.get("n", 0) for k, b in marg_bands.items() if k in ("0.88–0.90", "0.90–0.92", "0.92–0.93")
    )

    t60_cmp = next((c for c in comparisons if c["timing"] == "T60-22"), {})
    t75_cmp = next((c for c in comparisons if c["timing"] == "T75-22"), {})

    bullets = []
    if t60_cmp.get("marginal_ev") is not None and t60_cmp["marginal_ev"] < 0.5:
        bullets.append(
            f"Marginal favorite trades (ask>0.88) at T60 add only ${t60_cmp.get('delta_pnl', 0):+.2f} total; "
            f"marginal EV ${t60_cmp.get('marginal_ev', 0):+.2f}/trade — weak quality."
        )
    if high_ask_n:
        bullets.append(
            f"Ask bands 0.88–0.93 combined: {high_ask_n} trades, ${high_ask_pnl:+.2f} PnL "
            f"(avg ${high_ask_pnl/high_ask_n:+.2f}/trade)."
        )
    mid_band = marg_bands.get("0.80–0.88", {})
    if mid_band.get("n"):
        bullets.append(
            f"Best quality band 0.80–0.88: {mid_band['n']} trades, WR {mid_band['wr']}%, "
            f"EV ${mid_band['ev']:+.2f}/trade."
        )

    # Decision logic
    if t75_cmp.get("delta_pnl", 0) > 5 and (t75_cmp.get("marginal_wr") or 0) >= 92:
        decision = "T75-22 + fav cap 0.90 (compromise: earlier entry, modest cap extension)"
        chosen = "T75-22 · fav≤0.90"
    elif t60_cmp.get("delta_pnl", 0) <= 3 or (t60_cmp.get("marginal_ev") or 0) < 0.3:
        decision = "T75-22 + fav cap 0.88 (quality-first: keep 0.88, enter earlier only)"
        chosen = "T75-22 · fav≤0.88"
    else:
        decision = "T75-22 + fav cap 0.90"
        chosen = best_full.get("key", "T75-22 · fav≤0.90")

    if test_088_t75 and test_090_t75:
        bullets.append(
            f"Out-of-sample: T75 cap0.88 PnL ${test_088_t75['pnl']:+.2f} ({test_088_t75['n']} tr) vs "
            f"cap0.90 ${test_090_t75['pnl']:+.2f} ({test_090_t75['n']} tr)."
        )

    summary = (
        f"Best full-sample: {best_full.get('key', '?')} (${best_full.get('pnl', 0):+.2f}). "
        f"Suggested live config: {chosen}."
    )
    return {"summary": summary, "bullets": bullets, "decision": decision, "chosen": chosen}


if __name__ == "__main__":
    main()
