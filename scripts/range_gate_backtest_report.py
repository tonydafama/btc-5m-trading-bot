#!/usr/bin/env python3
"""Read-only range gate backtest (naive cut + pause/resume replay). No trading changes."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HKT = ZoneInfo("Asia/Hong_Kong")
TARGET_LEG = 6.0
ALLOWED_REGIMES = frozenset({"volatile_chop", "quiet_mixed"})

LIVE_BOOKS = [
    ("v2-live-desk (+$21 · old clock)", "logs/archive/v2_live_before_v3_20261104-110421/dual_leg_live_trades.json", "live", "legacy ~T-291"),
    ("v2-live-20261105-224129", "logs/archive/v2_live_closed_20261107-011349/dual_leg_live_trades.json", "live", "aligned checkpoints"),
    ("v2-live-20261107-011629", "logs/archive/v2_live_stop_v2-live-20261107-011629_20261108-094201/dual_leg_live_trades.json", "live", "aligned checkpoints"),
    ("v2-live-rg80-prep (current)", "logs/dual_leg_live_trades.json", "live", "legacy_poll + live Rg80 gate"),
]

PAPER_BOOKS = [
    ("paper-v2-100-20261103201732", "logs/archive/btc_v2_paper_20261107-010827/dual_leg_btc_paper_trades.json", "paper", "paper checkpoints"),
    ("paper-current", "logs/dual_leg_btc_paper_trades.json", "paper", "current paper session"),
]


def parse_hkt(ts: str) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).replace(" HKT", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=HKT)
        except ValueError:
            continue
    return None


def span_hkt(rows: list[dict]) -> tuple[datetime | None, datetime | None, float]:
    times: list[datetime] = []
    for r in rows:
        for k in ("opened_at_hkt", "settled_at_hkt", "closed_at_hkt"):
            dt = parse_hkt(str(r.get(k) or ""))
            if dt:
                times.append(dt)
    if not times:
        return None, None, 0.0
    t0, t1 = min(times), max(times)
    days = max((t1 - t0).total_seconds() / 86400.0, 1 / 24)
    return t0, t1, days


def range_usd(row: dict) -> float | None:
    er = row.get("entry_regime")
    if not isinstance(er, dict):
        er = row.get("binance_regime") if isinstance(row.get("binance_regime"), dict) else {}
    v = (er or {}).get("range_usd")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def regime_name(row: dict) -> str:
    er = row.get("entry_regime")
    if not isinstance(er, dict):
        er = row.get("binance_regime") if isinstance(row.get("binance_regime"), dict) else {}
    return str((er or {}).get("regime") or "missing")


def leg_unit(row: dict) -> float:
    for key in ("unit_usd", "stake", "leg_usd"):
        v = row.get(key)
        if v is not None:
            try:
                u = float(v)
                if u > 0:
                    return u
            except (TypeError, ValueError):
                pass
    legs = int(row.get("legs_posted") or 0) or int(row.get("legs_filled") or 0) or 1
    ts = row.get("total_stake")
    if ts is not None and legs > 0:
        try:
            return float(ts) / legs
        except (TypeError, ValueError):
            pass
    return 1.0


def outcome(row: dict) -> str:
    tr = str(row.get("trade_result") or "").upper()
    if tr in ("WIN", "LOSS", "BREAKEVEN", "BE", "PUSH"):
        return tr if tr != "BE" else "BREAKEVEN"
    pnl = float(row.get("pnl") or 0)
    if abs(pnl) < 0.01:
        return "BREAKEVEN"
    return "WIN" if pnl > 0 else "LOSS"


def scale_pnl(row: dict, target_leg: float = TARGET_LEG) -> float:
    u = leg_unit(row)
    mult = target_leg / u if u > 0 else 1.0
    return float(row.get("pnl") or 0) * mult


def wr_stats(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {"w": 0, "l": 0, "be": 0, "wr_pct": None}
    w = l = be = 0
    for r in rows:
        o = outcome(r)
        if o == "WIN":
            w += 1
        elif o == "LOSS":
            l += 1
        else:
            be += 1
    denom = w + l
    wr = round(100.0 * w / denom, 1) if denom else None
    return {"w": w, "l": l, "be": be, "wr_pct": wr}


def pack_rows(sub: list[dict]) -> dict[str, Any]:
    pnl_raw = round(sum(float(r.get("pnl") or 0) for r in sub), 2)
    pnl_sc = round(sum(scale_pnl(r) for r in sub), 2)
    one_leg = sum(1 for r in sub if int(r.get("legs_posted") or 0) < 2)
    return {
        "n": len(sub),
        "wr": wr_stats(sub),
        "pnl_raw": pnl_raw,
        "pnl_scaled_6usd_leg": pnl_sc,
        "one_leg": one_leg,
        "two_leg": len(sub) - one_leg,
    }


def slug_epoch(slug: str) -> int | None:
    m = re.search(r"-(\d{9,})$", str(slug or ""))
    return int(m.group(1)) if m else None


def build_windows(trades: list[dict]) -> list[dict]:
    by_slug: dict[str, dict] = {}
    epochs: list[int] = []
    for t in trades:
        slug = str(t.get("slug") or "")
        ep = slug_epoch(slug)
        if not slug or ep is None:
            continue
        by_slug[slug] = t
        epochs.append(ep)
    if not epochs:
        return []
    lo, hi = min(epochs), max(epochs)
    windows: list[dict] = []
    ep = lo
    while ep <= hi:
        slug = f"btc-updown-5m-{ep}"
        trade = by_slug.get(slug)
        if trade:
            rng = range_usd(trade)
            windows.append(
                {
                    "slug": slug,
                    "trade": trade,
                    "evaluated": rng is not None,
                    "range_usd": rng,
                }
            )
        else:
            windows.append({"slug": slug, "trade": None, "evaluated": False, "range_usd": None})
        ep += 300
    return windows


def simulate_gate(
    rows: list[dict],
    *,
    min_usd: float,
    resume_windows: int,
) -> dict[str, Any]:
    """Replay pause/resume; only windows with a settled trade contribute range_usd."""
    settled = [r for r in rows if isinstance(r, dict) and r.get("pnl") is not None]
    windows = build_windows(settled)
    paused = False
    consec_ok = 0
    kept: list[dict] = []
    skipped_pause = 0
    skipped_regime = 0
    skipped_low = 0

    for w in windows:
        if w["evaluated"]:
            rng = w["range_usd"]
            assert rng is not None
            if rng >= min_usd:
                consec_ok += 1
            else:
                consec_ok = 0
            if paused and consec_ok >= resume_windows:
                paused = False
                consec_ok = 0

        trade = w["trade"]
        if trade is None:
            continue
        if regime_name(trade) not in ALLOWED_REGIMES:
            skipped_regime += 1
            continue
        rng = range_usd(trade)
        if paused:
            skipped_pause += 1
            continue
        if rng is None:
            continue
        if rng < min_usd:
            skipped_low += 1
            paused = True
            consec_ok = 0
            continue
        kept.append(trade)

    before_allowed = [r for r in settled if regime_name(r) in ALLOWED_REGIMES]
    t0, t1, days = span_hkt(settled)
    after = pack_rows(kept)
    before = pack_rows(before_allowed)
    return {
        "span_hkt": f"{t0.strftime('%Y-%m-%d %H:%M') if t0 else '—'} → {t1.strftime('%Y-%m-%d %H:%M') if t1 else '—'}",
        "calendar_days": round(days, 3),
        "before_cut": before,
        "after_gate": after,
        "trades_before_n": before["n"],
        "trades_after_n": after["n"],
        "skipped_pause": skipped_pause,
        "skipped_low_range_entry": skipped_low,
        "forecast_30d_after_scaled": round(after["pnl_scaled_6usd_leg"] / days * 30, 2) if days and after["n"] else None,
        "forecast_30d_before_scaled": round(before["pnl_scaled_6usd_leg"] / days * 30, 2) if days and before["n"] else None,
    }


def analyze_naive(rows: list[dict], *, min_usd: float, strict_gt: bool = True) -> dict[str, Any]:
    settled = [r for r in rows if isinstance(r, dict) and r.get("pnl") is not None]
    t0, t1, days = span_hkt(settled)

    def pass_cut(r: dict) -> bool:
        rng = range_usd(r)
        if rng is None:
            return False
        return (rng > min_usd) if strict_gt else (rng >= min_usd)

    missing_rng = sum(1 for r in settled if range_usd(r) is None)
    kept = [r for r in settled if pass_cut(r)]
    before = pack_rows(settled)
    after = pack_rows(kept)
    return {
        "span_hkt": f"{t0.strftime('%Y-%m-%d %H:%M') if t0 else '—'} → {t1.strftime('%Y-%m-%d %H:%M') if t1 else '—'}",
        "calendar_days": round(days, 3),
        "before_cut": before,
        "after_naive": after,
        "trades_before_n": before["n"],
        "trades_after_n": after["n"],
        "missing_range_excluded": missing_rng,
        "forecast_30d_before_scaled": round(before["pnl_scaled_6usd_leg"] / days * 30, 2) if days and before["n"] else None,
        "forecast_30d_after_scaled": round(after["pnl_scaled_6usd_leg"] / days * 30, 2) if days and after["n"] else None,
    }


def dedupe_slug(rows: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for r in rows:
        slug = str(r.get("slug") or "")
        if not slug:
            continue
        prev = best.get(slug)
        if prev is None or (str(r.get("session_id") or "") and not str(prev.get("session_id") or "")):
            best[slug] = r
    return list(best.values())


def analyze_book(rows: list[dict], *, min_usd: float, resume_windows: int) -> dict[str, Any]:
    return {
        "naive_gt": analyze_naive(rows, min_usd=min_usd, strict_gt=True),
        "gate_resume": simulate_gate(rows, min_usd=min_usd, resume_windows=resume_windows),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="/root/btc-5m-bot")
    ap.add_argument("--min-usd", type=float, default=70.0)
    ap.add_argument("--resume-windows", type=int, default=1)
    args = ap.parse_args()
    root = Path(args.root)
    min_usd = float(args.min_usd)
    resume = int(args.resume_windows)

    out: dict[str, Any] = {
        "target_leg_usd": TARGET_LEG,
        "range_min_usd": min_usd,
        "resume_windows": resume,
        "naive_rule": f"entry_regime.range_usd > {min_usd}",
        "gate_rule": f"pause if range < {min_usd}; resume after {resume} consecutive evaluated window(s) >= {min_usd}",
        "caveat": "Replay only counts range on windows that have a trade row; gaps between trades do not advance resume.",
    }

    set1: dict[str, Any] = {}
    all_live: list[dict] = []
    for label, rel, kind, clock in LIVE_BOOKS:
        path = root / rel
        if not path.is_file():
            set1[label] = {"error": "missing"}
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            rows = []
        settled = [r for r in rows if isinstance(r, dict) and r.get("pnl") is not None]
        set1[label] = {**analyze_book(settled, min_usd=min_usd, resume_windows=resume), "kind": kind, "clock": clock}
        all_live.extend(settled)

    combined = dedupe_slug(all_live)
    set1["COMBINED_LIVE_dedup_slug"] = {
        **analyze_book(combined, min_usd=min_usd, resume_windows=resume),
        "kind": "live",
        "note": "dedupe by slug across live books",
    }

    paper_part: dict[str, Any] = {}
    all_paper: list[dict] = []
    for label, rel, kind, clock in PAPER_BOOKS:
        path = root / rel
        rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        if not isinstance(rows, list):
            rows = []
        settled = [r for r in rows if isinstance(r, dict) and r.get("pnl") is not None]
        paper_part[label] = {**analyze_book(settled, min_usd=min_usd, resume_windows=resume), "kind": kind, "clock": clock}
        all_paper.extend(settled)

    out["set1_live"] = set1
    out["set2_paper"] = paper_part
    out["set2_note"] = "Do not add live+paper PnL as one sample (overlapping windows). Paper ≠ live execution."

  # compact tables (finance style)
    def row_from_naive(src: str, v: dict) -> dict[str, Any]:
        b, a = v["before_cut"], v["after_naive"]
        return {
            "source": src,
            "method": "naive",
            "days": v.get("calendar_days"),
            "trades_before": b["n"],
            "trades_after": a["n"],
            "wr_before_pct": b["wr"]["wr_pct"],
            "wr_after_pct": a["wr"]["wr_pct"],
            "pnl_raw_after": a["pnl_raw"],
            "pnl_scaled_6leg_after": a["pnl_scaled_6usd_leg"],
            "forecast_30d_after": v.get("forecast_30d_after_scaled"),
        }

    def row_from_gate(src: str, v: dict) -> dict[str, Any]:
        b, a = v["before_cut"], v["after_gate"]
        return {
            "source": src,
            "method": f"gate_resume_{resume}",
            "days": v.get("calendar_days"),
            "trades_before": b["n"],
            "trades_after": a["n"],
            "wr_before_pct": b["wr"]["wr_pct"],
            "wr_after_pct": a["wr"]["wr_pct"],
            "pnl_raw_after": a["pnl_raw"],
            "pnl_scaled_6leg_after": a["pnl_scaled_6usd_leg"],
            "forecast_30d_after": v.get("forecast_30d_after_scaled"),
            "skipped_pause": v.get("skipped_pause"),
        }

    table: list[dict] = []
    for src, blob in {**set1, **paper_part}.items():
        if not isinstance(blob, dict) or "naive_gt" not in blob:
            continue
        table.append(row_from_naive(src, blob["naive_gt"]))
        table.append(row_from_gate(src, blob["gate_resume"]))
    out["finance_table"] = table

    # set3 per-book 30d (not averaged)
    set3: dict[str, Any] = {"method": "(scaled PnL at $6/leg ÷ calendar_days) × 30", "rows": []}
    for src, blob in {**set1, **paper_part}.items():
        if not isinstance(blob, dict) or "naive_gt" not in blob:
            continue
        n = blob["naive_gt"]
        g = blob["gate_resume"]
        set3["rows"].append(
            {
                "source": src,
                "naive_before_30d": n.get("forecast_30d_before_scaled"),
                "naive_after_30d": n.get("forecast_30d_after_scaled"),
                "gate_before_30d": g.get("forecast_30d_before_scaled"),
                "gate_after_30d": g.get("forecast_30d_after_scaled"),
            }
        )
    out["set3_forecast"] = set3

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
