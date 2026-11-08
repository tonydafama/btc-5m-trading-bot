#!/usr/bin/env python3
"""Export 18:00–24:00 HKT live entry-pause records for later backtest analysis.

Reads skip_entry_hkt_pause from dual_leg_live_daemon.jsonl, enriches with the
first fok_shadow would_enter signal per slug and any matching paper trade outcome.
Appends to logs/v2_hkt_pause_study/blocked_entries.jsonl (deduped by slug).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
STUDY_DIR = LOG_DIR / "v2_hkt_pause_study"
HKT = ZoneInfo("Asia/Hong_Kong")

DAEMON_LOG = LOG_DIR / "dual_leg_live_daemon.jsonl"
FOK_SHADOW = LOG_DIR / "dual_leg_fok_shadow.jsonl"
PAPER_TRADES = LOG_DIR / "dual_leg_btc_paper_trades.json"
OUT_JSONL = STUDY_DIR / "blocked_entries.jsonl"
SUMMARY = STUDY_DIR / "summary.json"


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _paper_by_slug() -> dict[str, dict]:
    trades = _read_json(PAPER_TRADES, [])
    if not isinstance(trades, list):
        return {}
    out: dict[str, dict] = {}
    for row in trades:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "")
        if slug:
            out[slug] = row
    return out


def _shadow_first_enter() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in _load_jsonl(FOK_SHADOW):
        if row.get("event") != "fok_shadow_eval":
            continue
        slug = str(row.get("slug") or "")
        if not slug or slug in out:
            continue
        out[slug] = {
            "shadow_would_enter_leg1": bool(row.get("would_enter_leg1")),
            "shadow_timestamp_hkt": row.get("timestamp_hkt"),
            "shadow_would_leg1_side": (row.get("would_leg1") or {}).get("side")
            if isinstance(row.get("would_leg1"), dict)
            else None,
        }
    return out


def _existing_slugs() -> set[str]:
    slugs: set[str] = set()
    for row in _load_jsonl(OUT_JSONL):
        slug = str(row.get("slug") or "")
        if slug:
            slugs.add(slug)
    return slugs


def main() -> None:
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    paper = _paper_by_slug()
    shadow = _shadow_first_enter()
    seen = _existing_slugs()
    appended = 0

    with OUT_JSONL.open("a", encoding="utf-8") as out_f:
        for row in _load_jsonl(DAEMON_LOG):
            if row.get("event") != "skip_entry_hkt_pause":
                continue
            slug = str(row.get("slug") or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            sh = shadow.get(slug, {})
            pt = paper.get(slug)
            record = {
                **row,
                **sh,
                "paper_had_trade": pt is not None,
                "paper_trade_result": pt.get("trade_result") if pt else None,
                "paper_pnl_usd": pt.get("pnl") if pt else None,
                "paper_opened_at_hkt": pt.get("opened_at_hkt") if pt else None,
                "exported_at_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            appended += 1

    all_rows = _load_jsonl(OUT_JSONL)
    would_enter = sum(1 for r in all_rows if r.get("would_enter_leg1") or r.get("shadow_would_enter_leg1"))
    paper_trades = sum(1 for r in all_rows if r.get("paper_had_trade"))
    paper_pnl = round(
        sum(float(r.get("paper_pnl_usd") or 0) for r in all_rows if r.get("paper_had_trade")),
        2,
    )
    summary = {
        "study": "v2_hkt_pause",
        "window_hkt": "18:00-24:00",
        "blocked_windows_total": len(all_rows),
        "exported_this_run": appended,
        "counterfactual_would_enter_count": would_enter,
        "paper_trades_during_blocked_windows": paper_trades,
        "paper_pnl_during_blocked_windows_usd": paper_pnl,
        "updated_at_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
        "paths": {
            "blocked_entries": str(OUT_JSONL),
            "daemon_log": str(DAEMON_LOG),
        },
    }
    SUMMARY.write_text(json.dumps(summary, indent=2) + "\n")

    print("OK v2 HKT pause study export")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
