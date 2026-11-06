"""Tail Parent V2 dual-leg logs for /logs (LIVE jsonl + paper trades JSON).

Read-only. Safe to import from Flask app.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

LIVE_EVENT_FILENAME = "dual_leg_live_daemon.jsonl"
PAPER_TRADES_FILENAME = "dual_leg_btc_paper_trades.json"


def _root(bot_root: Path | None) -> Path:
    return bot_root if bot_root is not None else Path(__file__).resolve().parent.parent


def _ts_key(row: dict[str, Any]) -> str:
    return str(
        row.get("timestamp_hkt")
        or row.get("timestamp_utc")
        or row.get("logged_at_hkt")
        or row.get("opened_at_hkt")
        or row.get("settled_at_hkt")
        or ""
    )


def _tail_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not path.is_file():
        return []
    buf: deque[dict[str, Any]] = deque(maxlen=min(limit + 128, 2048))
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    buf.append(obj)
    except OSError:
        return []
    return list(buf)[-limit:]


def _load_json_rows(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("trades"), list):
        return [r for r in raw["trades"] if isinstance(r, dict)]
    return []


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=_ts_key)


def _live_event_kind(ev: str) -> str:
    if ev in ("entry", "hedge", "trade_settled"):
        return "fill"
    if ev.startswith("skip_") or ev in ("entry_failed", "hedge_failed"):
        return "skip"
    if ev in ("live_daemon_start", "live_daemon_stop"):
        return "info"
    if ev == "loop_error":
        return "fail"
    return "info"


def _normalize_live_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["timestamp_hkt"] = out.get("timestamp_hkt") or ""
    ev = str(out.get("event") or "")
    kind = _live_event_kind(ev)
    if kind == "fill":
        out["trade_result"] = "FILLED"
        out["entered"] = True
    elif kind == "skip":
        out["trade_result"] = "SKIPPED"
        out["entered"] = False
    elif kind == "fail":
        out["trade_result"] = "FAILED"
    else:
        out.setdefault("trade_result", "")
    slug = str(out.get("slug") or "")
    note_parts = [ev] if ev else []
    if slug:
        note_parts.append(slug)
    if out.get("error"):
        note_parts.append(str(out.get("error")))
    elif out.get("skip_reasons"):
        note_parts.append(str(out.get("skip_reasons"))[:120])
    out["reason"] = " · ".join(note_parts) if note_parts else ev or json.dumps(out, default=str)[:200]
    out["slug"] = slug or ev or "—"
    out["bot"] = "dual-leg-live-v2"
    return out


def _normalize_paper_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["timestamp_hkt"] = _ts_key(out)
    res = str(out.get("trade_result") or out.get("result") or "").upper()
    if res in ("WIN", "LOSS", "TIE", "DRAW", "PUSH"):
        out["trade_result"] = res
        out["entered"] = True
    elif res in ("SKIPPED", "SKIP"):
        out["trade_result"] = "SKIPPED"
        out["entered"] = False
    elif out.get("pnl") is not None:
        out.setdefault("trade_result", "FILLED")
        out["entered"] = True
    out.setdefault("reason", out.get("reason") or out.get("strategy_reason") or "")
    out.setdefault("slug", str(out.get("slug") or "—"))
    out["bot"] = "dual-leg-paper-v2"
    return out


def tail_dual_leg_v2_logs(
    *,
    mode: str,
    ledger: str,
    limit: int,
    bot_root: Path | None = None,
) -> dict[str, Any]:
    """Return recent live daemon events or paper trade rows for Family Logs.

    mode: "all" | "trades" (fills / entries only, no pure skip lines for paper)
    ledger: "live" | "paper"
    """
    root = _root(bot_root)
    mode_l = (mode or "all").lower()
    ledger_l = (ledger or "live").lower()
    limit = min(max(int(limit), 1), 300)

    if ledger_l == "live":
        path = root / "logs" / LIVE_EVENT_FILENAME
        rows = [_normalize_live_row(r) for r in _tail_jsonl(path, limit * 3)]
        rows = _sort_rows(rows)
        # Re-sort descending for display (newest first)
        rows.sort(key=_ts_key, reverse=True)
        if mode_l == "trades":
            rows = [
                r
                for r in rows
                if str(r.get("trade_result") or "").upper() in ("FILLED", "WIN", "LOSS")
                or r.get("entered") is True
            ][:limit]
        else:
            rows = rows[:limit]
        stats_src = rows
    else:
        path = root / "logs" / PAPER_TRADES_FILENAME
        raw_rows = _load_json_rows(path)
        raw_rows = _sort_rows(raw_rows)
        # Newest last -> take tail, then reverse for display
        chunk = raw_rows[-limit * 3 :] if len(raw_rows) > limit * 3 else raw_rows
        rows = [_normalize_paper_row(r) for r in chunk]
        rows.reverse()
        if mode_l == "trades":

            def _paper_fill(r: dict[str, Any]) -> bool:
                res = str(r.get("trade_result") or "").upper()
                if res in ("WIN", "LOSS", "TIE", "DRAW", "PUSH", "FILLED"):
                    return True
                return r.get("pnl") is not None

            rows = [r for r in rows if _paper_fill(r)][:limit]
        else:
            rows = rows[:limit]
        stats_src = rows

    fills = 0
    skips = 0
    if ledger_l == "live":
        for r in stats_src:
            ev = str(r.get("event") or "")
            k = _live_event_kind(ev)
            if k == "fill":
                fills += 1
            elif k == "skip":
                skips += 1
    else:
        for r in stats_src:
            tr = str(r.get("trade_result") or "").upper()
            if tr in ("SKIPPED", "SKIP"):
                skips += 1
            elif tr in ("WIN", "LOSS", "FILLED", "FAILED", "FAIL"):
                fills += 1
            elif r.get("entered") is True:
                fills += 1
            elif r.get("entered") is False:
                skips += 1

    return {
        "bot": "dual-leg-live-v2" if ledger_l == "live" else "dual-leg-paper-v2",
        "ledger": ledger_l,
        "mode": mode_l,
        "lines": rows,
        "stats": {
            "fills": fills,
            "skips": skips,
            "trades": fills,
            "total": len(stats_src),
        },
        "source": path.name if path.is_file() else None,
        "source_file": path.name if path.is_file() else None,
    }
