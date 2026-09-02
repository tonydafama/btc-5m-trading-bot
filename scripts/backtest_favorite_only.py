#!/usr/bin/env python3
"""Backtest Package 2+ favorite-only vs full tiers (with fee estimate)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from new_strategy import calc_loss_pnl, calc_win_pnl
from scripts.backtest_package2_maxask import (
    START_BANKROLL,
    load_dataset,
    pick_snap,
    contract,
    max_drawdown,
)

FEE_RATE = 0.07
FAV_ASK = 0.70
MID_LO = 0.30


def est_fee(stake: float, ask: float) -> float:
    if ask <= 0:
        return 0.0
    shares = stake / ask
    return round(shares * FEE_RATE * ask * (1 - ask), 4)


def tier_for_ask(ask: float) -> str:
    if ask >= FAV_ASK:
        return "favorite"
    if ask > MID_LO:
        return "mid"
    return "underdog"


def run(
    slugs: list[str],
    win_by_slug: dict,
    outcomes: dict,
    *,
    favorite_only: bool,
    max_fav: float = 0.88,
    t_max: int = 75,
    t_min: int = 22,
) -> dict:
    bankroll = START_BANKROLL
    gross_pnl = 0.0
    fees = 0.0
    trades: list[dict] = []
    skips = {"timing": 0, "not_favorite": 0, "ask_cap": 0, "no_outcome": 0}

    for slug in slugs:
        outcome = outcomes.get(slug)
        if outcome not in ("UP", "DOWN"):
            skips["no_outcome"] += 1
            continue
        w = win_by_slug.get(slug)
        if not w:
            continue
        snap = pick_snap(w.get("checkpoints") or [], t_max, t_min, 0.025)
        if not snap:
            skips["timing"] += 1
            continue
        ask = float(snap.get("ask") or 0)
        if ask <= 0:
            skips["ask_cap"] += 1
            continue

        tier = tier_for_ask(ask)
        if favorite_only:
            if tier != "favorite":
                skips["not_favorite"] += 1
                continue
            if ask > max_fav:
                skips["ask_cap"] += 1
                continue
            stake = 7.0
        else:
            if tier == "favorite":
                cap = max_fav
                stake = 7.0
            elif tier == "mid":
                cap = 0.88
                stake = 5.0
            else:
                skips["not_favorite"] += 1
                continue  # skip underdog in full compare (matches live)
            if ask > cap:
                skips["ask_cap"] += 1
                continue

        side = snap.get("side", "up")
        won = contract(side) == outcome
        pnl = calc_win_pnl(stake, ask) if won else calc_loss_pnl(stake)
        fee = est_fee(stake, ask)
        gross_pnl += pnl
        fees += fee
        bankroll = round(bankroll + pnl - fee, 2)
        trades.append(
            {
                "slug": slug,
                "tier": tier,
                "ask": ask,
                "won": won,
                "pnl": pnl,
                "fee": fee,
                "net": round(pnl - fee, 2),
                "bankroll_after": bankroll,
            }
        )

    wins = sum(1 for t in trades if t["won"])
    losses = [t for t in trades if not t["won"]]
    loss_streak = 0
    max_ls = 0
    for t in trades:
        if not t["won"]:
            loss_streak += 1
            max_ls = max(max_ls, loss_streak)
        else:
            loss_streak = 0

    return {
        "mode": "favorite_only" if favorite_only else "full_p2_no_underdog",
        "max_fav": max_fav,
        "timing": f"T{t_max}-{t_min}",
        "trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "wr": round(wins / len(trades) * 100, 1) if trades else None,
        "gross_pnl": round(gross_pnl, 2),
        "fees": round(fees, 2),
        "net_pnl": round(gross_pnl - fees, 2),
        "final_bankroll": bankroll,
        "ev_gross": round(gross_pnl / len(trades), 2) if trades else None,
        "ev_net": round((gross_pnl - fees) / len(trades), 2) if trades else None,
        "max_dd_gross": max_drawdown(
            [{"bankroll_after": START_BANKROLL + sum(t["pnl"] for t in trades[: i + 1])} for i, t in enumerate(trades)]
        )
        if trades
        else 0,
        "max_dd_net": max_drawdown([{"bankroll_after": t["bankroll_after"]} for t in trades]),
        "max_loss_streak": max_ls,
        "losing_months_sim": sum(1 for t in losses),  # placeholder
        "skips": skips,
        "loss_trades": losses[:10],
    }


def main() -> None:
    slugs, win_by_slug, outcomes, ts_by_slug = load_dataset()
    slugs_sorted = sorted(set(slugs), key=lambda s: ts_by_slug.get(s, 0))
    n = len(slugs_sorted)
    split = int(n * 0.65)
    train = slugs_sorted[:split]
    test = slugs_sorted[split:]

    scenarios = [
        ("full P2 (no dog)", False, 0.90),
        ("full P2 (no dog)", False, 0.88),
        ("favorite-only", True, 0.90),
        ("favorite-only", True, 0.88),
    ]

    print("=" * 88)
    print(f"PACKAGE 2+ BACKTEST — favorite-only vs full · {n} windows w/ checkpoints")
    print(f"Train {len(train)} / Test {len(test)} · Start ${START_BANKROLL} · Fee est {FEE_RATE}×p(1-p)")
    print("=" * 88)

    results = {}
    for label, fav_only, cap in scenarios:
        key = f"{label} cap{cap}"
        results[key] = {
            "full": run(slugs_sorted, win_by_slug, outcomes, favorite_only=fav_only, max_fav=cap),
            "train": run(train, win_by_slug, outcomes, favorite_only=fav_only, max_fav=cap),
            "test": run(test, win_by_slug, outcomes, favorite_only=fav_only, max_fav=cap),
        }

    hdr = f"{'Scenario':<28} {'Set':<5} {'Tr':>4} {'WR':>6} {'Gross':>8} {'Fees':>7} {'Net':>8} {'MaxDD':>7}"
    print("\n" + hdr)
    print("-" * 88)
    for key, parts in results.items():
        for set_name in ("full", "train", "test"):
            r = parts[set_name]
            print(
                f"{key:<28} {set_name:<5} {r['trades']:>4} {str(r['wr'])+'%':>6} "
                f"${r['gross_pnl']:>+7.2f} ${r['fees']:>6.2f} ${r['net_pnl']:>+7.2f} ${r['max_dd_net']:>6.2f}"
            )

    # LIVE session replay (favorite-only filter on actual live trades)
    print("\n" + "=" * 88)
    print("LIVE SESSION REPLAY (live-20260616-p2plus closed trades)")
    print("=" * 88)
    live = json.loads((ROOT / "logs/trade_log_live.json").read_text())
    sid = json.loads((ROOT / "logs/live_session.json").read_text())["session_id"]
    closed = [
        r
        for r in live
        if r.get("session_id") == sid
        and r.get("strategy") == "PACKAGE2+"
        and r.get("trade_result") in ("WIN", "LOSS")
    ]
    full_gross = sum(float(r.get("pnl") or 0) for r in closed)
    fav = [r for r in closed if r.get("tier") == "favorite"]
    mid = [r for r in closed if r.get("tier") == "mid"]
    fav_gross = sum(float(r.get("pnl") or 0) for r in fav)
    mid_gross = sum(float(r.get("pnl") or 0) for r in mid)

    def live_fees(rows):
        return sum(est_fee(float(r.get("stake") or 0), float(r.get("entry_ask") or 0.5)) for r in rows)

    print(f"  All tiers:  {len(closed)} tr · gross ${full_gross:+.2f} · fees ~${live_fees(closed):.2f} · net ~${full_gross - live_fees(closed):+.2f}")
    print(f"  Favorite:   {len(fav)} tr · gross ${fav_gross:+.2f} · fees ~${live_fees(fav):.2f} · net ~${fav_gross - live_fees(fav):+.2f}")
    print(f"  Mid only:   {len(mid)} tr · gross ${mid_gross:+.2f} · fees ~${live_fees(mid):.2f} · net ~${mid_gross - live_fees(mid):+.2f}")
    print(f"  If fav-only from start: net ~${fav_gross - live_fees(fav):+.2f} vs actual ~${full_gross - live_fees(closed):+.2f}")

    out = ROOT / "logs/backtest_favorite_only.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
