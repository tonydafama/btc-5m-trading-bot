"""Dashboard helpers for midrange-vol strategy."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from midrange_vol_engine import (
    dashboard_snapshot,
    events_path,
    load_trades,
    trading_mode,
    live_armed,
)

BOT_ID = "midrange-vol-live"


def tail_events(limit: int = 80) -> list[dict[str, Any]]:
    path = events_path()
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _event_to_line(ev: dict[str, Any]) -> dict[str, Any]:
    name = str(ev.get("event") or "")
    slug = ev.get("slug") or ""
    ts = ev.get("timestamp_hkt") or ""
    line: dict[str, Any] = {
        "timestamp_hkt": ts,
        "slug": slug,
        "bot": "BTC",
        "mode": trading_mode(),
        "ledger": "live",
        "strategy_label": "先有波幅·中間價",
    }
    if name in ("entry", "entry_paper"):
        line.update(
            entered=True,
            trade_result="FILLED",
            side=ev.get("side"),
            reason=f"entry {ev.get('side')} @ {ev.get('ask')}",
        )
    elif name == "trade_settled":
        won = bool(ev.get("won"))
        line.update(
            entered=True,
            trade_result="WIN" if won else "LOSS",
            pnl=ev.get("pnl"),
            reason=f"settled pnl {ev.get('pnl')}",
        )
    elif name in ("entry_failed", "daemon_error"):
        line.update(entered=False, trade_result="FAIL", reason=ev.get("error") or name)
    elif name in ("skip", "wait_capital", "skip_low_cash"):
        line.update(
            entered=False,
            trade_result="SKIPPED",
            reason=ev.get("reason") or ev.get("note") or name,
        )
    else:
        line.update(entered=False, trade_result="SKIPPED", reason=name)
    return line


def _trade_rows_as_lines() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in load_trades():
        res = (
            "WIN"
            if t.get("won")
            else ("LOSS" if t.get("status") == "settled" else "FILLED")
        )
        out.append(
            {
                "timestamp_hkt": t.get("settled_at_hkt") or t.get("opened_at_hkt"),
                "slug": t.get("slug"),
                "trade_result": res,
                "entered": True,
                "pnl": t.get("pnl"),
                "side": t.get("side"),
                "reason": f"{t.get('side')} stake ${t.get('stake')}",
                "mode": trading_mode(),
            }
        )
    return out


def family_bot_status() -> dict[str, Any]:
    running = False
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "midrange-vol-live.service"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        running = r.stdout.strip() == "active"
    except Exception:
        running = False
    snap = dashboard_snapshot()
    armed = live_armed()
    seat = "真倉 ON" if armed else "等入金 · armed=0"
    run = "跑緊" if running else "服務未啟"
    return {
        "id": BOT_ID,
        "display_name": "先有波幅·中間價",
        "status": f"{seat} · {run}",
        "status_line": f"{seat} · {run}",
        "mode": trading_mode(),
        "live": armed,
        "armed": armed,
        "running": running,
        "paused": not running,
        "summary": snap,
    }


def tail_midvol_for_family(*, mode: str = "all", limit: int = 40) -> dict[str, Any]:
    t0 = time.perf_counter()
    events = tail_events(400)
    lines_from_ev = [_event_to_line(e) for e in events]
    lines_from_trades = _trade_rows_as_lines()
    merged: dict[str, dict[str, Any]] = {}
    for row in lines_from_ev + lines_from_trades:
        key = f"{row.get('timestamp_hkt')}|{row.get('slug')}|{row.get('trade_result')}"
        merged[key] = row
    rows = sorted(
        merged.values(),
        key=lambda r: str(r.get("timestamp_hkt") or ""),
        reverse=True,
    )
    stats = {"fills": 0, "skips": 0, "total": len(rows)}
    for r in rows:
        tr = str(r.get("trade_result") or "").upper()
        if tr in ("WIN", "LOSS", "FILLED", "ENTER"):
            stats["fills"] += 1
        elif tr in ("SKIPPED", "SKIP"):
            stats["skips"] += 1
    stats["trades"] = stats["fills"]
    mode_l = (mode or "all").lower()
    if mode_l == "trades":
        view = [
            r
            for r in rows
            if str(r.get("trade_result") or "").upper() in ("WIN", "LOSS", "FILLED", "ENTER")
        ][:limit]
    else:
        view = rows[:limit]
    snap = dashboard_snapshot()
    st = snap.get("stats") or {}
    return {
        "bot": BOT_ID,
        "mode": mode_l,
        "lines": view,
        "stats": stats,
        "source": "midrange_vol_events.jsonl",
        "source_file": "midrange_vol_events.jsonl",
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "seat_label": "Live 席 · 中間價單邊",
        "running_label": f"{'ARMED' if live_armed() else 'WAIT CAPITAL'} · {trading_mode()}",
        "midvol_summary": {
            "pnl_usd": st.get("pnl_usd"),
            "settled": st.get("settled"),
            "live_armed": snap.get("live_armed"),
            "wallet_cash_usdc": snap.get("wallet_cash_usdc"),
        },
    }
