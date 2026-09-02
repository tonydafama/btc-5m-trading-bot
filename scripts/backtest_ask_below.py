#!/usr/bin/env python3
"""Backtest: buy when ask in band (e.g. ultra-dog < 0.15) with slippage + depth filters.

Uses checkpoint snapshots from logs/windows.json (BTC) and logs/eth_windows.json (ETH).
Outcome from window outcome / outcome_estimate. Not a fill replay — see module docstring limits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from new_strategy import calc_loss_pnl, calc_win_pnl

WINDOW_FILES = {
    "btc": ROOT / "logs" / "windows.json",
    "eth": ROOT / "logs" / "eth_windows.json",
}

DEFAULTS = {
    "btc": {"min_depth": 50.0, "max_spread": 0.06, "t_min": 22, "t_max": 75},
    "eth": {"min_depth": 30.0, "max_spread": 0.06, "t_min": 22, "t_max": 75},
}


def load_windows(asset: str) -> list[dict]:
    path = WINDOW_FILES[asset.lower()]
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data if isinstance(data, list) else []


def window_outcome(w: dict) -> Optional[str]:
    o = w.get("outcome") or w.get("outcome_estimate")
    if o in ("up", "down"):
        return o
    return None


def book_for_side(cp: dict, side: str) -> dict:
    key = "up_book" if side == "up" else "down_book"
    return cp.get(key) or {}


def passes_filters(
    book: dict,
    *,
    min_depth: float,
    max_spread: float,
) -> tuple[bool, str]:
    ask = book.get("best_ask")
    spread = book.get("spread")
    depth = float(book.get("ask_depth_usd") or 0)
    if ask is None:
        return False, "no_ask"
    if spread is not None and float(spread) > max_spread:
        return False, f"spread>{max_spread}"
    if depth < min_depth:
        return False, f"depth<{min_depth:.0f}"
    return True, "ok"


def effective_ask(raw_ask: float, *, slippage_abs: float, slippage_pct: float) -> float:
    slip = slippage_abs + raw_ask * slippage_pct
    return min(0.99, round(raw_ask + slip, 4))


def pick_momentum(cp: dict, ask_min: float, ask_max: float) -> Optional[tuple[str, float]]:
    side = cp.get("side")
    ask = cp.get("ask")
    if side not in ("up", "down") or ask is None:
        return None
    ask = float(ask)
    if ask < ask_min or ask >= ask_max:
        return None
    return side, ask


def pick_cheapest(cp: dict, ask_min: float, ask_max: float) -> Optional[tuple[str, float]]:
    best: Optional[tuple[str, float]] = None
    for side in ("up", "down"):
        ask = (book_for_side(cp, side).get("best_ask"))
        if ask is None:
            continue
        ask = float(ask)
        if ask_min <= ask < ask_max:
            if best is None or ask < best[1]:
                best = (side, ask)
    return best


def pick_entry(
    checkpoints: list[dict],
    *,
    mode: str,
    t_min: float,
    t_max: float,
    ask_min: float,
    ask_max: float,
    min_depth: float,
    max_spread: float,
    pick_rule: str,
) -> Optional[dict]:
    """pick_rule: first = earliest in window (max t_remaining); best_ask = lowest ask in window."""
    cands: list[dict] = []
    for cp in checkpoints:
        t = float(cp.get("t_remaining") or 999)
        if not (t_min <= t <= t_max):
            continue
        if mode == "momentum":
            picked = pick_momentum(cp, ask_min, ask_max)
        else:
            picked = pick_cheapest(cp, ask_min, ask_max)
        if not picked:
            continue
        side, raw_ask = picked
        book = book_for_side(cp, side)
        ok, reason = passes_filters(book, min_depth=min_depth, max_spread=max_spread)
        if not ok:
            continue
        cands.append(
            {
                "side": side,
                "raw_ask": raw_ask,
                "t_remaining": t,
                "checkpoint": cp.get("checkpoint"),
                "depth": book.get("ask_depth_usd"),
                "spread": book.get("spread"),
                "filter": reason,
            }
        )
    if not cands:
        return None
    if pick_rule == "best_ask":
        return min(cands, key=lambda c: c["raw_ask"])
    return max(cands, key=lambda c: float(c["t_remaining"]))


def run_backtest(
    asset: str,
    *,
    stake: float,
    mode: str,
    ask_min: float,
    ask_max: float,
    slippage_abs: float,
    slippage_pct: float,
    pick_rule: str,
    t_min: float,
    t_max: float,
    min_depth: float,
    max_spread: float,
) -> dict[str, Any]:
    windows = load_windows(asset)
    trades: list[dict] = []
    stats = {"no_outcome": 0, "no_signal": 0, "filtered_all": 0}

    for w in windows:
        outcome = window_outcome(w)
        if not outcome:
            stats["no_outcome"] += 1
            continue
        entry = pick_entry(
            w.get("checkpoints") or [],
            mode=mode,
            t_min=t_min,
            t_max=t_max,
            ask_min=ask_min,
            ask_max=ask_max,
            min_depth=min_depth,
            max_spread=max_spread,
            pick_rule=pick_rule,
        )
        if not entry:
            stats["no_signal"] += 1
            continue

        fill_ask = effective_ask(entry["raw_ask"], slippage_abs=slippage_abs, slippage_pct=slippage_pct)
        won = entry["side"] == outcome
        pnl = calc_win_pnl(stake, fill_ask) if won else calc_loss_pnl(stake)
        trades.append(
            {
                "slug": w.get("slug"),
                "side": entry["side"],
                "raw_ask": entry["raw_ask"],
                "fill_ask": fill_ask,
                "t_remaining": entry["t_remaining"],
                "outcome": outcome,
                "won": won,
                "pnl": pnl,
                "depth": entry.get("depth"),
            }
        )

    wins = sum(1 for t in trades if t["won"])
    losses = len(trades) - wins
    total_pnl = round(sum(t["pnl"] for t in trades), 2)
    bankroll_path = [round(sum(x["pnl"] for x in trades[: i + 1]), 2) for i in range(len(trades))]
    peak = 0.0
    max_dd = 0.0
    for b in bankroll_path:
        peak = max(peak, b)
        max_dd = max(max_dd, peak - b)

    return {
        "asset": asset.upper(),
        "mode": mode,
        "ask_band": f"[{ask_min:.2f}, {ask_max:.2f})",
        "stake_usd": stake,
        "slippage": f"+{slippage_abs:.3f} + {slippage_pct:.1%} of ask",
        "filters": f"depth≥${min_depth:.0f} spread≤{max_spread}",
        "timing": f"T-{int(t_max)}..T-{int(t_min)} pick={pick_rule}",
        "windows_total": len(windows),
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(100 * wins / len(trades), 1) if trades else None,
        "total_pnl": total_pnl,
        "avg_raw_ask": round(sum(t["raw_ask"] for t in trades) / len(trades), 4) if trades else None,
        "avg_fill_ask": round(sum(t["fill_ask"] for t in trades) / len(trades), 4) if trades else None,
        "max_drawdown": round(max_dd, 2),
        "stats": stats,
        "sample": trades[:8],
    }


def print_report(report: dict) -> None:
    print(f"\n{'=' * 60}")
    print(
        f"{report['asset']} · {report['mode']} · ask {report['ask_band']} · "
        f"${report['stake_usd']:.2f}/trade"
    )
    print(f"  {report['slippage']} · {report['filters']} · {report['timing']}")
    print(f"  windows={report['windows_total']} trades={report['trades']} "
          f"({report['wins']}W/{report['losses']}L WR={report['win_rate_pct']}%)")
    print(f"  total PnL=${report['total_pnl']:+.2f} · max DD=${report['max_drawdown']:.2f}")
    if report.get("avg_raw_ask") is not None:
        print(f"  avg ask raw={report['avg_raw_ask']} fill={report['avg_fill_ask']}")
    st = report.get("stats") or {}
    print(f"  skipped: no_outcome={st.get('no_outcome')} no_signal={st.get('no_signal')}")
    for t in report.get("sample") or []:
        print(
            f"    {t['slug'][-10:]} {t['side']} raw={t['raw_ask']:.3f} fill={t['fill_ask']:.3f} "
            f"T-{t['t_remaining']:.0f} → {t['outcome']} {'WIN' if t['won'] else 'LOSS'} ${t['pnl']:+.2f}"
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Backtest ask-band entries from window checkpoints")
    p.add_argument("--asset", choices=("btc", "eth", "both"), default="both")
    p.add_argument("--mode", choices=("momentum", "cheapest"), default="momentum")
    p.add_argument("--ask-min", type=float, default=0.06, help="inclusive lower ask bound")
    p.add_argument("--ask-max", type=float, default=0.15, help="exclusive upper ask bound")
    p.add_argument("--stake", type=float, default=2.0)
    p.add_argument("--slippage", type=float, default=0.01, help="absolute add to ask")
    p.add_argument("--slippage-pct", type=float, default=0.0, help="fraction of ask added")
    p.add_argument("--min-depth", type=float, default=None)
    p.add_argument("--max-spread", type=float, default=None)
    p.add_argument("--t-min", type=float, default=None)
    p.add_argument("--t-max", type=float, default=None)
    p.add_argument("--pick", choices=("first", "best_ask"), default="first")
    args = p.parse_args()

    assets = ["btc", "eth"] if args.asset == "both" else [args.asset]
    for asset in assets:
        cfg = DEFAULTS[asset]
        report = run_backtest(
            asset,
            stake=args.stake,
            mode=args.mode,
            ask_min=args.ask_min,
            ask_max=args.ask_max,
            slippage_abs=args.slippage,
            slippage_pct=args.slippage_pct,
            pick_rule=args.pick,
            t_min=args.t_min if args.t_min is not None else cfg["t_min"],
            t_max=args.t_max if args.t_max is not None else cfg["t_max"],
            min_depth=args.min_depth if args.min_depth is not None else cfg["min_depth"],
            max_spread=args.max_spread if args.max_spread is not None else cfg["max_spread"],
        )
        print_report(report)

    print(
        "\nNote: checkpoint snapshots only (~8/window). Fill at raw_ask+slippage; "
        "no fees; not exact live replay.\n"
    )


if __name__ == "__main__":
    main()
