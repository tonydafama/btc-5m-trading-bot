#!/usr/bin/env python3
"""Backtest late-window favorite snipe (near close, high ask, small edge).

Uses observer checkpoints in logs/windows.json. Outcomes:
official `outcome`, else `outcome_estimate`, else BTC open vs close.

Not live-truth: checkpoints are sparse (~T-75..5); buy quotes at T-12/5 are often
one-sided (loser ask ~0.01). We model favourite side and tradable ask explicitly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from polymarket_fees import estimate_taker_fee


def calc_win_pnl(stake: float, ask: float) -> float:
    if ask <= 0:
        return 0.0
    shares = stake / ask
    return round(shares - stake, 4)


def calc_loss_pnl(stake: float) -> float:
    return round(-stake, 4)

DEFAULT_STAKE = 4.0
MIN_DEPTH = 0.0
MAX_SPREAD = 0.12


def load_windows(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def settle_outcome(rec: dict[str, Any]) -> Optional[str]:
    raw = (rec.get("outcome") or rec.get("outcome_estimate") or "").strip().lower()
    if raw in ("up", "down"):
        return raw.upper()
    open_p = rec.get("open_btc_price")
    close_p = rec.get("close_btc_price")
    if open_p is None or close_p is None:
        return None
    return "UP" if float(close_p) >= float(open_p) else "DOWN"


def nearest_checkpoint(checkpoints: list[dict], t_target: float, *, tol: float = 4.0) -> Optional[dict]:
    best: Optional[dict] = None
    for cp in checkpoints:
        t = float(cp.get("t_remaining") or 0)
        if abs(t - t_target) > tol:
            continue
        if best is None or abs(t - t_target) < abs(float(best.get("t_remaining") or 0) - t_target):
            best = cp
    return best


def pick_snap_in_band(checkpoints: list[dict], t_min: float, t_max: float) -> Optional[dict]:
    """Latest snapshot inside [t_min, t_max] (closest to window end)."""
    cands = [c for c in checkpoints if t_min <= float(c.get("t_remaining") or 0) <= t_max]
    if not cands:
        return None
    return min(cands, key=lambda c: float(c.get("t_remaining") or 999))


def favorite_quote(snap: dict[str, Any]) -> tuple[Optional[str], Optional[float], dict[str, Any]]:
    """Return (side, ask, meta) for the market favourite at this snapshot."""
    up_book = snap.get("up_book") or {}
    down_book = snap.get("down_book") or {}
    up = up_book.get("best_ask")
    down = down_book.get("best_ask")
    up_f = float(up) if up is not None else None
    down_f = float(down) if down is not None else None
    delta = float(snap.get("delta_usd") or 0)

    # Two-sided obvious favourite (winner ask high, loser ask penny)
    if down_f is not None and up_f is not None:
        if down_f < 0.08 and up_f >= 0.85:
            return "up", up_f, {"pattern": "two_sided", "book": "up"}
        if up_f < 0.08 and down_f >= 0.85:
            return "down", down_f, {"pattern": "two_sided", "book": "down"}

    # Single visible high ask
    candidates: list[tuple[str, float]] = []
    if up_f is not None and up_f >= 0.50:
        candidates.append(("up", up_f))
    if down_f is not None and down_f >= 0.50:
        candidates.append(("down", down_f))
    if candidates:
        side, ask = max(candidates, key=lambda x: x[1])
        return side, ask, {"pattern": "high_ask", "book": side}

    # One-sided penny — favourite is the other leg; no seller quote
    if down_f is not None and down_f < 0.08 and up_f is None:
        return "up", None, {"pattern": "inferred_up", "loser_ask": down_f}
    if up_f is not None and up_f < 0.08 and down_f is None:
        return "down", None, {"pattern": "inferred_down", "loser_ask": up_f}

    side = "up" if delta >= 0 else "down"
    book = up_book if side == "up" else down_book
    ask = book.get("best_ask")
    ask_f = float(ask) if ask is not None else None
    return side, ask_f, {"pattern": "delta", "book": side}


def passes_liquidity(
    snap: dict[str, Any],
    side: str,
    *,
    min_depth: float,
    max_spread: float,
) -> tuple[bool, str]:
    book = (snap.get("up_book") if side == "up" else snap.get("down_book")) or {}
    depth = float(book.get("ask_depth_usd") or 0)
    spread = book.get("spread")
    if depth + 1e-9 < min_depth:
        return False, f"depth_{depth:.0f}_lt_{min_depth:.0f}"
    if spread is not None and float(spread) > max_spread + 1e-12:
        return False, f"spread_{float(spread):.3f}_gt_{max_spread}"
    return True, "ok"


def simulate_trade(
    *,
    stake: float,
    ask: float,
    won: bool,
) -> dict[str, float]:
    shares = stake / ask
    fee = estimate_taker_fee(shares, ask)
    gross = calc_win_pnl(stake, ask) if won else calc_loss_pnl(stake)
    net = round(gross - fee, 4)
    ret_pct = round(100.0 * net / stake, 3) if stake > 0 else 0.0
    return {"gross": round(gross, 4), "fee": fee, "net": net, "ret_pct": ret_pct}


def run_scenario(
    windows: list[dict],
    *,
    t_min: float,
    t_max: float,
    ask_min: float,
    ask_max: float,
    min_depth: float,
    max_spread: float,
    stake: float,
    pick_mode: str,
) -> dict[str, Any]:
    skips: dict[str, int] = {}
    trades: list[dict] = []

    def skip(reason: str) -> None:
        skips[reason] = skips.get(reason, 0) + 1

    for rec in windows:
        slug = rec.get("slug") or ""
        outcome = settle_outcome(rec)
        if outcome not in ("UP", "DOWN"):
            skip("no_outcome")
            continue
        cps = rec.get("checkpoints") or []
        if pick_mode == "latest_in_band":
            snap = pick_snap_in_band(cps, t_min, t_max)
        else:
            # target = midpoint band, fall back to latest in band
            target = (t_min + t_max) / 2.0
            snap = nearest_checkpoint(cps, target) or pick_snap_in_band(cps, t_min, t_max)
        if not snap:
            skip("no_checkpoint")
            continue
        t_rem = float(snap.get("t_remaining") or 0)
        if not (t_min <= t_rem <= t_max):
            skip("timing")
            continue

        side, ask, meta = favorite_quote(snap)
        if side is None:
            skip("no_side")
            continue
        if ask is None:
            skip("no_fav_ask")
            continue
        if ask < ask_min or ask > ask_max:
            skip("ask_band")
            continue
        ok, liq = passes_liquidity(snap, side, min_depth=min_depth, max_spread=max_spread)
        if not ok:
            skip(liq)
            continue

        won = (side == "up" and outcome == "UP") or (side == "down" and outcome == "DOWN")
        pnl = simulate_trade(stake=stake, ask=ask, won=won)
        trades.append(
            {
                "slug": slug,
                "t_remaining": round(t_rem, 1),
                "side": side,
                "ask": round(ask, 4),
                "outcome": outcome,
                "won": won,
                "pattern": meta.get("pattern"),
                **pnl,
            }
        )

    n = len(trades)
    wins = sum(1 for t in trades if t["won"])
    gross = round(sum(t["gross"] for t in trades), 2)
    fees = round(sum(t["fee"] for t in trades), 2)
    net = round(sum(t["net"] for t in trades), 2)
    wr = round(100.0 * wins / n, 2) if n else None
    ev = round(net / n, 4) if n else None
    avg_win_ret = round(
        sum(t["ret_pct"] for t in trades if t["won"]) / max(1, wins), 3
    ) if wins else None
    avg_loss = round(sum(t["net"] for t in trades if not t["won"]) / max(1, n - wins), 2) if n - wins else None

    return {
        "t_band": f"T-{t_max:.0f}→{t_min:.0f}",
        "ask_band": f"[{ask_min:.2f},{ask_max:.2f}]",
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "wr_pct": wr,
        "gross_pnl": gross,
        "fees": fees,
        "net_pnl": net,
        "ev_per_trade_usd": ev,
        "avg_win_ret_pct": avg_win_ret,
        "avg_loss_usd": avg_loss,
        "skips": skips,
        "trades": trades,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Late favorite snipe backtest (windows.json checkpoints)")
    p.add_argument("--windows", default=os.getenv("WINDOWS_JSON", str(ROOT / "logs/windows.json")))
    p.add_argument("--stake", type=float, default=DEFAULT_STAKE)
    p.add_argument("--out", default=str(ROOT / "logs/backtest_late_favorite_snipe.json"))
    args = p.parse_args()

    path = Path(args.windows)
    if not path.is_file():
        print(f"Missing {path}", file=sys.stderr)
        sys.exit(1)

    windows = load_windows(path)
    with_outcome = sum(1 for w in windows if settle_outcome(w))

    timing_grid = [
        (8, 3, "latest_in_band"),
        (12, 5, "latest_in_band"),
        (20, 8, "latest_in_band"),
        (25, 12, "latest_in_band"),
        (30, 15, "latest_in_band"),
        (35, 20, "latest_in_band"),
        (40, 25, "latest_in_band"),
    ]
    ask_bands = [
        (0.88, 0.94),
        (0.90, 0.96),
        (0.92, 0.97),
        (0.94, 0.98),
        (0.96, 0.995),
        (0.97, 0.995),
        (0.98, 0.999),
    ]

    grid: list[dict] = []
    for t_max, t_min, mode in timing_grid:
        for ask_lo, ask_hi in ask_bands:
            r = run_scenario(
                windows,
                t_min=float(t_min),
                t_max=float(t_max),
                ask_min=ask_lo,
                ask_max=ask_hi,
                min_depth=MIN_DEPTH,
                max_spread=MAX_SPREAD,
                stake=float(args.stake),
                pick_mode=mode,
            )
            grid.append({k: v for k, v in r.items() if k != "trades"})

    # Attach best + transparency on ask availability by T band
    coverage: list[dict] = []
    for t_max, t_min, _ in timing_grid:
        n_ask = no_ask = 0
        for rec in windows:
            snap = pick_snap_in_band(rec.get("checkpoints") or [], t_min, t_max)
            if not snap:
                continue
            _, ask, _ = favorite_quote(snap)
            if ask is not None and ask >= 0.88:
                n_ask += 1
            else:
                no_ask += 1
        coverage.append({"t_band": f"T-{t_max}→{t_min}", "fav_ask_ge_088": n_ask, "no_tradable_fav_ask": no_ask})

    ranked = sorted(grid, key=lambda x: (x["net_pnl"], x["n"]), reverse=True)
    payload = {
        "strategy": "late_favorite_snipe",
        "windows_file": str(path),
        "windows_total": len(windows),
        "windows_with_outcome": with_outcome,
        "stake_usd": args.stake,
        "fee_model": "polymarket_crypto_7pct",
        "note_zh": (
            "尾盤用 checkpoints 模擬買大熱門。T-12/5 經常只得輸家盤 0.01、贏家無 ask，"
            "故多數場景樣本細；結算用 window outcome_estimate / BTC open-close。"
        ),
        "quote_coverage": coverage,
        "grid": grid,
        "top_by_net_pnl": ranked[:12],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 72)
    print(f"LATE FAVORITE SNIPE · {len(windows)} windows · {with_outcome} w/ outcome · stake ${args.stake}")
    print("=" * 72)
    print(f"{'T band':<14} {'Ask band':<16} {'N':>4} {'WR%':>6} {'Net$':>8} {'EV/tr':>7} {'Skips(no_ask)':>12}")
    print("-" * 72)
    for row in ranked[:15]:
        na = row["skips"].get("no_fav_ask", 0)
        wr = f"{row['wr_pct']:.1f}" if row["wr_pct"] is not None else "—"
        ev = f"{row['ev_per_trade_usd']:+.3f}" if row["ev_per_trade_usd"] is not None else "—"
        print(
            f"{row['t_band']:<14} {row['ask_band']:<16} {row['n']:>4} {wr:>6} "
            f"${row['net_pnl']:>+7.2f} {ev:>7} {na:>12}"
        )
    print()
    print("Quote coverage (fav ask≥0.88 in timing band):")
    for c in coverage:
        print(f"  {c['t_band']}: {c['fav_ask_ge_088']} quotes · {c['no_tradable_fav_ask']} missing/low ask")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
