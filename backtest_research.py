"""Backtest research — session/tier breakdown for US-hours pause analysis."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot_common import (
    ETH_LIVE_TRADE_LOG_FILE,
    ETH_TRADE_LOG_FILE,
    LIVE_TRADE_LOG_FILE,
    LOG_DIR,
    PAPER_TRADE_LOG_FILE,
)

ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
BACKTEST_TAPE_FILE = LOG_DIR / "backtest_tape.jsonl"
BACKTEST_TAPE_STATE_FILE = LOG_DIR / "backtest_tape_state.json"
SESSION_RESEARCH_FILE = LOG_DIR / "session_research_live.json"

PERIODS = ("us_hours", "core", "outside_us", "all")
TIERS = ("favorite", "underdog", "all")


def parse_hkt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(str(ts).replace(" HKT", "")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=HKT)
    except ValueError:
        return None


def in_us_weekday(dt_et: datetime) -> bool:
    if dt_et.weekday() >= 5:
        return False
    m = dt_et.hour * 60 + dt_et.minute
    return 9 * 60 + 30 <= m < 16 * 60


def in_core(dt_et: datetime) -> bool:
    if not in_us_weekday(dt_et):
        return False
    m = dt_et.hour * 60 + dt_et.minute
    return 11 * 60 <= m < 15 * 60


def trade_period(ts: str | None) -> str:
    dt = parse_hkt(ts)
    if dt is None:
        return "unknown"
    et = dt.astimezone(ET)
    if in_core(et):
        return "core"
    if in_us_weekday(et):
        return "us_hours"
    return "outside_us"


def trade_tier(row: dict[str, Any]) -> str:
    tier = str(row.get("tier") or "").lower()
    if tier in ("favorite", "fav"):
        return "favorite"
    if tier in ("underdog", "dog"):
        return "underdog"
    ask = float(row.get("entry_ask") or row.get("decision_ask") or 0)
    if ask >= 0.70:
        return "favorite"
    if 0.15 <= ask < 0.30:
        return "underdog"
    return "other"


def closed_entered_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [
        r
        for r in data
        if r.get("entered") and r.get("trade_result") in ("WIN", "LOSS")
    ]


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "wins": 0, "losses": 0, "wr_pct": None, "pnl": 0.0, "avg_pnl": None}
    wins = sum(1 for r in rows if r.get("trade_result") == "WIN")
    pnl = round(sum(float(r.get("pnl") or 0) for r in rows), 2)
    n = len(rows)
    return {
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "wr_pct": round(100.0 * wins / n, 1),
        "pnl": pnl,
        "avg_pnl": round(pnl / n, 2),
    }


def _tier_period_breakdown(rows: list[dict[str, Any]], *, asset: str) -> dict[str, Any]:
    buckets: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        ts = r.get("timestamp_hkt") or r.get("settled_at_hkt")
        period = trade_period(ts)
        tier = trade_tier(r)
        if tier == "other":
            continue
        buckets[period][tier].append(r)
        buckets[period]["all"].append(r)
        buckets["all"][tier].append(r)
        buckets["all"]["all"].append(r)

    out: dict[str, Any] = {}
    for period in PERIODS:
        out[period] = {}
        for tier in TIERS:
            out[period][tier] = _stats(buckets[period][tier])
    out["asset"] = asset
    return out


def build_session_research(
    *,
    btc_rows: list[dict[str, Any]] | None = None,
    eth_rows: list[dict[str, Any]] | None = None,
    source: str = "live",
) -> dict[str, Any]:
    if btc_rows is None:
        btc_rows = closed_entered_rows(LIVE_TRADE_LOG_FILE)
    if eth_rows is None:
        eth_rows = closed_entered_rows(ETH_LIVE_TRADE_LOG_FILE)

    btc = _tier_period_breakdown(btc_rows, asset="BTC")
    eth = _tier_period_breakdown(eth_rows, asset="ETH")

    def _counterfactual(asset_breakdown: dict, period: str, tier: str) -> dict[str, Any]:
        cell = asset_breakdown.get(period, {}).get(tier, {})
        total = asset_breakdown.get("all", {}).get("all", {})
        pnl_if_paused = round(float(total.get("pnl") or 0) - float(cell.get("pnl") or 0), 2)
        return {
            "paused_period": period,
            "paused_tier": tier,
            "trades_removed": cell.get("n", 0),
            "pnl_removed": cell.get("pnl", 0),
            "pnl_without_these_trades": pnl_if_paused,
            "wise_to_pause": float(cell.get("pnl") or 0) < 0,
        }

    scenarios = []
    for asset_label, bd in (("BTC", btc), ("ETH", eth)):
        for period in ("us_hours", "outside_us", "core"):
            for tier in ("favorite", "underdog"):
                cf = _counterfactual(bd, period, tier)
                if cf["trades_removed"]:
                    cf["asset"] = asset_label
                    scenarios.append(cf)

    now = datetime.now(timezone.utc).astimezone(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")
    return {
        "generated_at_hkt": now,
        "source": source,
        "research_question": (
            "If we pause fav/dog during US hours, core, or outside-US — which improves PnL?"
        ),
        "period_definitions": {
            "us_hours": "Mon–Fri 09:30–16:00 ET (excludes core overlap counted separately in core)",
            "core": "Mon–Fri 11:00–15:00 ET",
            "outside_us": "Evenings, weekends, non-NYSE window",
        },
        "btc": btc,
        "eth": eth,
        "pause_scenarios": sorted(
            scenarios,
            key=lambda x: (not x["wise_to_pause"], x["pnl_removed"]),
        ),
        "notes": [
            "wise_to_pause=True means that bucket lost money — skipping it would have helped historically.",
            "This is descriptive backtest only; sample size may be small for ETH dog / outside-US.",
            "Use backtest_tape.jsonl for custom filters; full logs in backtest_archive/.",
        ],
    }


def tape_trade_id(asset: str, row: dict[str, Any]) -> str:
    slug = row.get("slug") or row.get("market") or "unknown"
    ts = row.get("timestamp_hkt") or row.get("settled_at_hkt") or ""
    return f"{asset.lower()}:{slug}:{ts[:19]}"


def normalize_tape_row(asset: str, row: dict[str, Any], *, source: str = "live") -> dict[str, Any]:
    ts = row.get("timestamp_hkt") or row.get("settled_at_hkt")
    sess = row.get("trading_session") if isinstance(row.get("trading_session"), dict) else {}
    return {
        "id": tape_trade_id(asset, row),
        "asset": asset.upper(),
        "slug": row.get("slug"),
        "timestamp_hkt": ts,
        "period": trade_period(ts),
        "tier": trade_tier(row),
        "stake": round(float(row.get("stake") or 0), 2),
        "entry_ask": row.get("entry_ask"),
        "trade_result": row.get("trade_result"),
        "pnl": round(float(row.get("pnl") or 0), 2),
        "in_trading_session": sess.get("in_trading_session"),
        "session_id": row.get("session_id"),
        "source_log": source,
    }


def _load_tape_state() -> dict[str, Any]:
    if not BACKTEST_TAPE_STATE_FILE.exists():
        return {"seen_ids": []}
    try:
        return json.loads(BACKTEST_TAPE_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"seen_ids": []}


def _save_tape_state(state: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    seen = state.get("seen_ids") or []
    if len(seen) > 50000:
        seen = seen[-40000:]
    state["seen_ids"] = seen
    state["updated_at_hkt"] = datetime.now(timezone.utc).astimezone(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")
    BACKTEST_TAPE_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def append_rows_to_tape(rows: list[dict[str, Any]], *, asset: str, source: str = "live") -> int:
    """Append closed trades to permanent jsonl (never cleared on PnL reset)."""
    if not rows:
        return 0
    state = _load_tape_state()
    seen = set(state.get("seen_ids") or [])
    added = 0
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with BACKTEST_TAPE_FILE.open("a") as f:
        for row in rows:
            if not row.get("entered") or row.get("trade_result") not in ("WIN", "LOSS"):
                continue
            tid = tape_trade_id(asset, row)
            if tid in seen:
                continue
            rec = normalize_tape_row(asset, row, source=source)
            f.write(json.dumps(rec, default=str) + "\n")
            seen.add(tid)
            added += 1
    state["seen_ids"] = list(seen)
    _save_tape_state(state)
    return added


def sync_backtest_tape_from_logs() -> dict[str, Any]:
    """Backfill tape from all log sources (live + paper + archive not included)."""
    sources = [
        ("BTC", LIVE_TRADE_LOG_FILE, "btc_live"),
        ("ETH", ETH_LIVE_TRADE_LOG_FILE, "eth_live"),
        ("BTC", PAPER_TRADE_LOG_FILE, "btc_paper"),
        ("ETH", ETH_TRADE_LOG_FILE, "eth_paper"),
    ]
    total = 0
    detail: dict[str, int] = {}
    for asset, path, label in sources:
        rows = closed_entered_rows(path)
        n = append_rows_to_tape(rows, asset=asset, source=label)
        detail[label] = n
        total += n
    return {"ok": True, "appended": total, "by_source": detail}


def refresh_live_session_research() -> dict[str, Any]:
    research = build_session_research(source="live")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_RESEARCH_FILE.write_text(json.dumps(research, indent=2) + "\n")
    return research


def build_session_research_from_paths(
    btc_path: Path,
    eth_path: Path,
    *,
    source: str,
) -> dict[str, Any]:
    return build_session_research(
        btc_rows=closed_entered_rows(btc_path),
        eth_rows=closed_entered_rows(eth_path),
        source=source,
    )
