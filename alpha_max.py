"""Alpha-Max Hour Cells paper strategy.

Paper-only challenger mined from historical closed trades. It does not place
orders; BTC/ETH observers call it at window finalization using collected
checkpoints and the resolved market outcome.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot_common import LOG_DIR
from new_strategy import (
    bought_label,
    calc_loss_pnl,
    calc_win_pnl,
    now_hkt_str,
    resolved_label,
    side_to_contract,
    trade_result,
)

HKT = ZoneInfo("Asia/Hong_Kong")
ET = ZoneInfo("America/New_York")

LOG_FILE = LOG_DIR / "alpha_max_trades.json"
SESSION_FILE = LOG_DIR / "alpha_max_session.json"

STAKE_RULES = {
    "BTC": {"favorite": 27.0, "underdog": 45.0, "core_mult": 1.5},
    "ETH": {"favorite": 24.0, "underdog": 45.0, "core_mult": 1.5},
}

# Positive historical cells by (asset, period, tier, ET hour), n >= 2.
ALPHA_CELLS: set[tuple[str, str, str, int]] = {
    ("BTC", "core", "favorite", 11),
    ("BTC", "core", "favorite", 12),
    ("BTC", "core", "favorite", 14),
    ("BTC", "core", "underdog", 11),
    ("BTC", "core", "underdog", 14),
    ("BTC", "outside_us", "favorite", 0),
    ("BTC", "outside_us", "favorite", 2),
    ("BTC", "outside_us", "favorite", 4),
    ("BTC", "outside_us", "favorite", 5),
    ("BTC", "outside_us", "favorite", 6),
    ("BTC", "outside_us", "favorite", 8),
    ("BTC", "outside_us", "favorite", 10),
    ("BTC", "outside_us", "favorite", 11),
    ("BTC", "outside_us", "favorite", 12),
    ("BTC", "outside_us", "favorite", 13),
    ("BTC", "outside_us", "favorite", 17),
    ("BTC", "outside_us", "favorite", 19),
    ("BTC", "us_hours", "favorite", 9),
    ("BTC", "us_hours", "favorite", 15),
    ("BTC", "us_hours", "underdog", 10),
    ("ETH", "core", "favorite", 11),
    ("ETH", "core", "favorite", 12),
    ("ETH", "core", "favorite", 14),
    ("ETH", "core", "underdog", 11),
    ("ETH", "outside_us", "favorite", 3),
    ("ETH", "outside_us", "favorite", 8),
    ("ETH", "outside_us", "favorite", 18),
    ("ETH", "outside_us", "underdog", 5),
    ("ETH", "outside_us", "underdog", 16),
    ("ETH", "us_hours", "favorite", 9),
    ("ETH", "us_hours", "favorite", 15),
    ("ETH", "us_hours", "underdog", 10),
    ("ETH", "us_hours", "underdog", 15),
}


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def ensure_session() -> dict[str, Any]:
    sess = _load_json(SESSION_FILE, {})
    if sess.get("started_at_hkt"):
        return sess
    now = datetime.now(timezone.utc)
    sess = {
        "session_id": "alpha-max-" + now.strftime("%Y%m%d-%H%M%S"),
        "strategy": "Alpha-Max Hour Cells",
        "mode": "paper",
        "started_at_utc": now.isoformat(),
        "started_at_hkt": now.astimezone(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
        "compare_from": "activation_time",
        "note": "Paper-only challenger; compare against V1 from this timestamp.",
    }
    _save_json(SESSION_FILE, sess)
    return sess


def load_alpha_trades() -> list[dict[str, Any]]:
    rows = _load_json(LOG_FILE, [])
    return rows if isinstance(rows, list) else []


def _save_alpha_trades(rows: list[dict[str, Any]]) -> None:
    _save_json(LOG_FILE, rows[-1000:])


def _dt_from_snapshot(snapshot: dict[str, Any]) -> datetime:
    raw = snapshot.get("ts")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(ET)
        except ValueError:
            pass
    return datetime.now(ET)


def period_for_dt(dt_et: datetime) -> str:
    minute = dt_et.hour * 60 + dt_et.minute
    if dt_et.weekday() < 5 and 11 * 60 <= minute < 15 * 60:
        return "core"
    if dt_et.weekday() < 5 and 9 * 60 + 30 <= minute < 16 * 60:
        return "us_hours"
    return "outside_us"


def tier_for_ask(ask: float) -> str:
    if ask >= 0.70:
        return "favorite"
    if 0.15 <= ask < 0.30:
        return "underdog"
    return "skip"


def stake_for(asset: str, tier: str, period: str) -> float:
    rules = STAKE_RULES[asset]
    base = rules[tier]
    if period == "core":
        base *= rules["core_mult"]
        try:
            from polymarket_wallet import cached_wallet_cash

            wallet = float(cached_wallet_cash() or 0)
        except Exception:
            wallet = 0.0
        base *= max(1.0, round(wallet / 1000.0, 4))
    return round(base, 2)


def _timing_ok(snapshot: dict[str, Any]) -> bool:
    blockers = [
        r for r in (snapshot.get("skip_reasons") or [])
        if not str(r).startswith("ask_")
    ]
    return bool(snapshot.get("in_entry_window", True)) and not blockers


def _candidate(asset: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    ask = snapshot.get("ask")
    if ask is None:
        return None
    ask = float(ask)
    if ask <= 0 or ask > 0.90 or not _timing_ok(snapshot):
        return None
    tier = tier_for_ask(ask)
    if tier == "skip":
        return None
    dt_et = _dt_from_snapshot(snapshot)
    period = period_for_dt(dt_et)
    cell = (asset, period, tier, dt_et.hour)
    if cell not in ALPHA_CELLS:
        return None
    return {
        "asset": asset,
        "tier": tier,
        "period": period,
        "hour_et": dt_et.hour,
        "ask": ask,
        "side": snapshot.get("side", "up"),
        "checkpoint": snapshot.get("checkpoint"),
        "t_remaining": snapshot.get("t_remaining"),
        "cell": cell,
    }


def alpha_skip_reason(asset: str, snapshot: dict[str, Any]) -> str:
    """Human-readable skip reason for live log (mirrors _candidate gates)."""
    asset = asset.upper()
    ask = snapshot.get("ask")
    if ask is None:
        return "Skipped: no ask"
    ask = float(ask)
    if ask <= 0:
        return "Skipped: invalid ask"
    if ask > 0.90:
        return f"Skipped: ask {ask:.2f} > 0.90 cap"
    if not _timing_ok(snapshot):
        blockers = [str(r) for r in (snapshot.get("skip_reasons") or []) if not str(r).startswith("ask_")]
        detail = ", ".join(blockers) if blockers else "outside entry window"
        return f"Skipped: timing/filter — {detail}"
    tier = tier_for_ask(ask)
    if tier == "skip":
        return f"Skipped: ask {ask:.2f} not favorite/underdog tier"
    dt_et = _dt_from_snapshot(snapshot)
    period = period_for_dt(dt_et)
    hour = dt_et.hour
    cell_key = f"{asset}|{period}|{tier}|{hour:02d} ET"
    if (asset, period, tier, hour) not in ALPHA_CELLS:
        return f"Skipped: not in Alpha cell ({cell_key})"
    return "Skipped: eligible cell (should enter)"


def pick_alpha_candidate(asset: str, checkpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [_candidate(asset, c) for c in checkpoints]
    candidates = [c for c in candidates if c]
    if not candidates:
        return None
    # Best economics among eligible paper entries: lower ask buys more shares.
    return min(candidates, key=lambda c: c["ask"])


def log_alpha_window(
    *,
    asset: str,
    slug: str,
    title: str,
    checkpoints: list[dict[str, Any]],
    outcome: str | None,
) -> dict[str, Any] | None:
    ensure_session()
    try:
        from alpha_max_research import record_window_evaluation

        record_window_evaluation(
            asset=asset,
            slug=slug,
            title=title,
            checkpoints=checkpoints,
            outcome=outcome,
            source="paper",
        )
    except Exception:
        pass

    rows = load_alpha_trades()
    if any(r.get("slug") == slug for r in rows):
        return None

    asset = asset.upper()
    cand = pick_alpha_candidate(asset, checkpoints)
    if not cand:
        return None
    if outcome not in ("up", "down"):
        return None

    contract = side_to_contract(cand["side"])
    resolved = "UP" if outcome == "up" else "DOWN"
    result = trade_result(resolved, contract, True)
    stake = stake_for(asset, cand["tier"], cand["period"])
    pnl = calc_win_pnl(stake, cand["ask"]) if result == "WIN" else calc_loss_pnl(stake)
    row = {
        "timestamp_hkt": now_hkt_str(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": "ALPHA-MAX",
        "mode": "paper",
        "asset": asset,
        "slug": slug,
        "market": title,
        "tier": cand["tier"],
        "period": cand["period"],
        "hour_et": cand["hour_et"],
        "checkpoint": cand["checkpoint"],
        "t_remaining": cand["t_remaining"],
        "side": cand["side"],
        "bought_contract": contract,
        "bought_label": bought_label(contract, cand["ask"], True),
        "entry_ask": round(cand["ask"], 4),
        "decision_ask": round(cand["ask"], 4),
        "stake": stake,
        "stake_intended": stake,
        "multiplier": "alpha-max",
        "entered": True,
        "filled": True,
        "resolved_outcome": resolved,
        "resolved_label": resolved_label(resolved, contract),
        "trade_result": result,
        "pnl": round(pnl, 2),
        "status": "closed",
        "strategy_reason": (
            f"Alpha-Max {asset} {cand['period']} {cand['tier']} "
            f"{cand['hour_et']:02d}:00 ET cell"
        ),
    }
    try:
        from trade_analytics import enrich_alpha_row, enrich_settlement_pnl

        enrich_settlement_pnl(row)
        row = enrich_alpha_row(row, snapshot=cand, checkpoints=checkpoints)
    except Exception:
        pass
    rows.append(row)
    _save_alpha_trades(rows)
    return row


def _parse_hkt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).replace(" HKT", "")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=HKT)
    except ValueError:
        return None


def _closed_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if r.get("trade_result") in ("WIN", "LOSS")]
    wins = [r for r in closed if r.get("trade_result") == "WIN"]
    pnl = round(sum(float(r.get("pnl") or 0) for r in closed), 2)
    return {
        "trades_total": len(rows),
        "trades_closed": len(closed),
        "trades_pending": 0,
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "win_rate": round(100 * len(wins) / len(closed), 1) if closed else None,
        "total_pnl": pnl,
        "overall_pnl": pnl,
    }


def _asset_breakdown(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for asset in ("BTC", "ETH"):
        asset_rows = [r for r in rows if str(r.get("asset") or "").upper() == asset]
        out[asset.lower()] = _closed_stats(asset_rows)
    return out


def _v1_rows_since(start_hkt: str | None) -> list[dict[str, Any]]:
    start = _parse_hkt(start_hkt)
    rows: list[dict[str, Any]] = []
    for path in (LOG_DIR / "trade_log_live.json", LOG_DIR / "trade_log_eth_live.json"):
        data = _load_json(path, [])
        if not isinstance(data, list):
            continue
        asset = "ETH" if "eth" in path.name else "BTC"
        for row in data:
            if not row.get("entered") or row.get("trade_result") not in ("WIN", "LOSS"):
                continue
            ts = _parse_hkt(row.get("timestamp_hkt"))
            if start and ts and ts < start:
                continue
            row = dict(row)
            row["asset"] = row.get("asset") or asset
            rows.append(row)
    return rows


def alpha_max_summary() -> dict[str, Any]:
    sess = ensure_session()
    rows = load_alpha_trades()
    stats = _closed_stats(rows)
    today = now_hkt_str()[:10]
    today_rows = [r for r in rows if str(r.get("timestamp_hkt", "")).startswith(today)]
    v1_rows = _v1_rows_since(sess.get("started_at_hkt"))
    v1_stats = _closed_stats(v1_rows)
    by_asset = _asset_breakdown(rows)
    v1_by_asset = _asset_breakdown(v1_rows)
    out = {
        "strategy": "ALPHA-MAX",
        "mode": "paper",
        "stake_usd": None,
        "min_edge": None,
        "daily_limit": 999,
        "daily_entered": len(today_rows),
        "daily_pnl_hkt": round(sum(float(r.get("pnl") or 0) for r in today_rows), 2),
        "started_at_hkt": sess.get("started_at_hkt"),
        "compare_from": sess.get("compare_from"),
        "rules_count": len(ALPHA_CELLS),
        "v1_since_start": v1_stats,
        "by_asset": by_asset,
        "v1_by_asset": v1_by_asset,
        "odds_api_key_set": True,
    }
    out.update(stats)
    try:
        from alpha_max_research import RESEARCH_CACHE, _load_json as _rj

        cached = _rj(RESEARCH_CACHE, {})
        if cached:
            out["research_snapshot"] = {
                "go_live": cached.get("go_live"),
                "net_pnl_est": (cached.get("summary") or {}).get("net_pnl_est"),
            }
    except Exception:
        pass
    return out
