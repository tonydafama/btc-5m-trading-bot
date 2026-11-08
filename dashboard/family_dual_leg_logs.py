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
PAPER_SESSION_FILENAME = "dual_leg_paper_session.json"
V2_LIVE_ENV = "config/v2_parent_live_exec.env"


def _root(bot_root: Path | None) -> Path:
    return bot_root if bot_root is not None else Path(__file__).resolve().parent.parent


def _read_kv_session(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _current_live_session_id(root: Path) -> str:
    return _read_kv_session(root / V2_LIVE_ENV, "DUAL_LEG_LIVE_SESSION_ID")


def _current_paper_session_id(root: Path) -> str:
    path = root / "logs" / PAPER_SESSION_FILENAME
    if path.is_file():
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                sid = str(obj.get("session_id") or "").strip()
                if sid:
                    return sid
        except (OSError, json.JSONDecodeError):
            pass
    return ""


def _ts_key(row: dict[str, Any]) -> str:
    return str(
        row.get("timestamp_hkt")
        or row.get("timestamp_utc")
        or row.get("logged_at_hkt")
        or row.get("opened_at_hkt")
        or row.get("settled_at_hkt")
        or ""
    )


def _tail_jsonl_raw(path: Path, max_lines: int) -> list[dict[str, Any]]:
    if max_lines <= 0 or not path.is_file():
        return []
    buf: deque[dict[str, Any]] = deque(maxlen=max_lines)
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
    return list(buf)


def _after_last_live_start(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last_start = -1
    for i, r in enumerate(rows):
        if str(r.get("event") or "") == "live_daemon_start":
            last_start = i
    if last_start >= 0:
        return rows[last_start:]
    return rows


def _filter_live_session_rows(rows: list[dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
    """Keep only the current live session.

    Prefer rows tagged with ``session_id`` (new daemon). If none are tagged yet,
    keep lines after the last ``live_daemon_start`` (one process run). If the log
    records a ``live_daemon_start`` with matching ``session_id``, slice from there.
    """
    sid = (session_id or "").strip()
    if not sid:
        return _after_last_live_start(rows)
    tagged = [r for r in rows if str(r.get("session_id") or "").strip() == sid]
    if tagged:
        return tagged
    cut = 0
    for i, r in enumerate(rows):
        if str(r.get("event") or "") != "live_daemon_start":
            continue
        if str(r.get("session_id") or "").strip() == sid:
            cut = i
    if cut > 0:
        return rows[cut:]
    return _after_last_live_start(rows)


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
    if ev == "trade_settled":
        tr = str(out.get("trade_result") or "").upper()
        if tr in ("WIN", "LOSS"):
            out["trade_result"] = tr
        elif out.get("pnl") is not None:
            try:
                p = float(out["pnl"])
                out["trade_result"] = "WIN" if p > 1e-9 else "LOSS" if p < -1e-9 else "PUSH"
            except (TypeError, ValueError):
                out["trade_result"] = tr or "SETTLED"
        else:
            out["trade_result"] = tr or "SETTLED"
        out["entered"] = True
    elif kind == "fill":
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
    if ev == "trade_settled":
        try:
            pnl_v = out.get("pnl")
            pnl_f = float(pnl_v) if pnl_v is not None else None
        except (TypeError, ValueError):
            pnl_f = None
        oc = str(out.get("outcome") or "").upper()
        if pnl_f is not None:
            sign = "+" if pnl_f > 0 else ""
            note_parts = [f"PnL {sign}${pnl_f:.2f}", oc or "—", slug or "—"]
        else:
            note_parts = [f"結算 {out.get('trade_result') or '—'}", oc or "", slug or "—"]
        out["log_pnl_usd"] = pnl_f
    elif ev == "skip_rg80":
        rng = out.get("range_usd")
        note_parts = [
            f"Rg80 · range {rng}" if rng is not None else "Rg80",
            f"paused={out.get('rg80_paused')}",
        ]
    elif out.get("error"):
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
    slug = str(out.get("slug") or "—")
    try:
        pnl_f = float(out["pnl"]) if out.get("pnl") is not None else None
    except (TypeError, ValueError):
        pnl_f = None
    if pnl_f is not None:
        sign = "+" if pnl_f > 0 else ""
        out["reason"] = f"PnL {sign}${pnl_f:.2f} · {out.get('trade_result') or '—'} · {slug}"
        out["log_pnl_usd"] = pnl_f
    else:
        out.setdefault("reason", out.get("reason") or out.get("strategy_reason") or "")
    out.setdefault("slug", slug)
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

    live_session = ""
    paper_session = ""
    if ledger_l == "live":
        path = root / "logs" / LIVE_EVENT_FILENAME
        live_session = _current_live_session_id(root)
        raw = _tail_jsonl_raw(path, max_lines=12000)
        raw = _filter_live_session_rows(raw, live_session)
        rows = [_normalize_live_row(r) for r in raw]
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
        paper_session = _current_paper_session_id(root)
        raw_rows = _load_json_rows(path)
        ps = (paper_session or "").strip()
        if ps:
            matched = [r for r in raw_rows if str(r.get("session_id") or "").strip() == ps]
            # Legacy rows may omit session_id; then the open file is already this session.
            if not matched:
                legacy = [r for r in raw_rows if not str(r.get("session_id") or "").strip()]
                raw_rows = legacy if legacy else raw_rows
            else:
                raw_rows = matched
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

    settles = 0
    posts = 0
    skips = 0
    info = 0
    fills = 0  # legacy: posts + settles (meaningful trading activity)
    if ledger_l == "live":
        for r in stats_src:
            ev = str(r.get("event") or "")
            if ev == "trade_settled":
                settles += 1
            elif ev in ("entry", "hedge"):
                posts += 1
            elif ev.startswith("skip_") or ev in ("entry_failed", "hedge_failed"):
                skips += 1
            elif _live_event_kind(ev) == "info" or ev == "loop_error":
                info += 1
        fills = posts + settles
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

    try:
        from desk_bots import v2_strategy_display_name

        strategy_label = v2_strategy_display_name(root)
    except Exception:
        strategy_label = "V2 21+ Rg80"

    return {
        "bot": "dual-leg-live-v2" if ledger_l == "live" else "dual-leg-paper-v2",
        "strategy_label": strategy_label,
        "display_name": strategy_label,
        "ledger": ledger_l,
        "mode": mode_l,
        "lines": rows,
        "session_id": live_session if ledger_l == "live" else paper_session,
        "stats": {
            "fills": fills,
            "skips": skips,
            "trades": settles if ledger_l == "live" else fills,
            "settles": settles,
            "posts": posts,
            "info": info,
            "total": len(stats_src),
        },
        "source": path.name if path.is_file() else None,
        "source_file": path.name if path.is_file() else None,
    }
