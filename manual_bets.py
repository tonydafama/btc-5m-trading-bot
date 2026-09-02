"""Manual Polymarket bets — excluded from bot PnL, shown separately on dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path("/root/btc-5m-bot")
MANUAL_BETS_FILE = ROOT / "logs" / "manual_bets.json"
HKT = timezone(timedelta(hours=8))


def _today_hkt() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d")


def load_manual_bets() -> list[dict[str, Any]]:
    if not MANUAL_BETS_FILE.exists():
        return []
    try:
        data = json.loads(MANUAL_BETS_FILE.read_text())
    except json.JSONDecodeError:
        return []
    bets = data.get("bets") if isinstance(data, dict) else data
    return list(bets or [])


def load_manual_bets_meta() -> dict[str, Any]:
    if not MANUAL_BETS_FILE.exists():
        return {}
    try:
        data = json.loads(MANUAL_BETS_FILE.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        k: data[k]
        for k in (
            "last_sync_hkt",
            "note",
            "version",
            "pnl_baseline_hkt",
            "archived_tx_hashes",
            "archived_bet_count",
        )
        if k in data
    }


def session_baseline_hkt() -> str | None:
    """HKT timestamp floor for session-scoped manual PnL (capital launch or V1 session)."""
    meta = load_manual_bets_meta()
    if meta.get("pnl_baseline_hkt"):
        return str(meta["pnl_baseline_hkt"])[:19]
    try:
        sess_path = ROOT / "logs" / "live_session.json"
        if sess_path.exists():
            sess = json.loads(sess_path.read_text())
            started = str(sess.get("started_at_hkt") or "")[:19]
            if started:
                return started
    except (json.JSONDecodeError, OSError):
        pass
    return None


def active_manual_bets(bets: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Bets counted for dashboard PnL (after capital-launch baseline if set)."""
    rows = bets if bets is not None else load_manual_bets()
    baseline = session_baseline_hkt()
    if not baseline:
        return rows
    return [b for b in rows if str(b.get("timestamp_hkt") or "")[:19] >= baseline]


def session_manual_bets(bets: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Manual bets since current V1 session start (same scope as bot trade logs)."""
    return active_manual_bets(bets)


def save_manual_bets(bets: list[dict[str, Any]], *, note: str | None = None) -> None:
    from manual_bets_sync import save_manual_bets_file

    save_manual_bets_file(bets, note=note)


def manual_bets_summary(*, today: str | None = None, auto_sync: bool = True) -> dict[str, Any]:
    if auto_sync:
        try:
            from manual_bets_sync import maybe_sync_manual_bets

            maybe_sync_manual_bets()
        except Exception:
            pass
    today = today or _today_hkt()
    all_bets = load_manual_bets()
    bets = session_manual_bets(all_bets)
    closed = [b for b in bets if b.get("result") in ("WIN", "LOSS") and b.get("pnl_usd") is not None]
    daily = [b for b in closed if str(b.get("timestamp_hkt") or "")[:10] == today]
    daily_pnl = round(sum(float(b.get("pnl_usd") or 0) for b in daily), 2)
    session_pnl = round(sum(float(b.get("pnl_usd") or 0) for b in closed), 2)
    all_closed = [
        b for b in all_bets if b.get("result") in ("WIN", "LOSS") and b.get("pnl_usd") is not None
    ]
    overall_pnl = round(sum(float(b.get("pnl_usd") or 0) for b in all_closed), 2)
    wins = sum(1 for b in closed if b.get("result") == "WIN")
    losses = len(closed) - wins
    open_count = sum(1 for b in bets if b.get("result") == "OPEN")
    meta = load_manual_bets_meta()
    return {
        "count": len(bets),
        "closed": len(closed),
        "open_count": open_count,
        "wins": wins,
        "losses": losses,
        "daily_pnl_hkt": daily_pnl,
        "session_pnl": session_pnl,
        "overall_pnl": overall_pnl,
        "overall_pnl_all_time": overall_pnl,
        "bets": bets,
        "session_bets": bets,
        "today_hkt": today,
        "session_baseline_hkt": session_baseline_hkt(),
        "last_sync_hkt": meta.get("last_sync_hkt"),
        "excluded_from_bot": True,
        "note": "Session manual PnL is excluded from BTC/ETH bot totals.",
    }
