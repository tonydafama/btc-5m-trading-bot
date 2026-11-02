"""Tail bot trade JSON logs for Family Logs UI (/logs).

Trades mode returns **fills only** — never silently falls back to skip lines.
Stats always include skip/fill counts for empty-state copy.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

def _root() -> Path:
    return Path(os.getenv("BTC_5M_ROOT", str(Path(__file__).resolve().parent)))


def _log_dir() -> Path:
    return _root() / "logs"


def _normalize_bot_key(bot: str) -> str:
    return (bot or "").strip().lower().replace(" ", "-")


def _resolve_log_path(bot_key: str) -> Path | None:
    key = _normalize_bot_key(bot_key)
    if not key:
        return None
    candidates = [
        _log_dir() / "sons" / f"{key}_trades.json",
        _log_dir() / f"{key}_trades.json",
        _log_dir() / f"{key}.json",
        _log_dir() / "bots" / f"{key}_trades.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("trades"), list):
        return [r for r in raw["trades"] if isinstance(r, dict)]
    return []


def is_skip_row(row: dict[str, Any]) -> bool:
    res = str(row.get("trade_result") or row.get("result") or row.get("status") or "").upper()
    if res in ("SKIPPED", "SKIP"):
        return True
    if row.get("entered") is False:
        return True
    if not row.get("entered") and res not in ("WIN", "LOSS", "FAILED", "FAIL", "ENTER", "FILLED"):
        note = str(row.get("strategy_reason") or row.get("reason") or row.get("slug") or "").upper()
        if "SKIP" in note:
            return True
    return False


def is_fill_row(row: dict[str, Any]) -> bool:
    if is_skip_row(row):
        return False
    res = str(row.get("trade_result") or row.get("result") or "").upper()
    if res in ("WIN", "LOSS", "FAILED", "FAIL", "FILLED"):
        return True
    return bool(row.get("entered"))


def live_seat_label(bot_key: str, row_sample: dict[str, Any] | None = None) -> str:
    """Wording for live unit file — avoid plain 「paper」 beside Son U Live lock."""
    key = _normalize_bot_key(bot_key)
    mode = str((row_sample or {}).get("mode") or (row_sample or {}).get("ledger") or "").lower()
    if "ultimate-live" in key or key.endswith("-live"):
        if mode == "live":
            return "Live 席 · 真倉 ON"
        return "Live 席 · 紙上預備"
    if mode == "live":
        return "Live 席 · 真倉"
    if mode == "paper":
        return "Paper 模擬"
    return "Live 席 · 紙上預備"


def tail_bot_log(
    bot_key: str,
    *,
    mode: str = "trades",
    limit: int = 40,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    path = _resolve_log_path(bot_key)
    rows = _load_rows(path) if path else []
    rows = sorted(
        rows,
        key=lambda r: str(r.get("timestamp_hkt") or r.get("timestamp_utc") or r.get("logged_at") or ""),
        reverse=True,
    )
    stats = {"fills": 0, "skips": 0, "total": len(rows)}
    for r in rows:
        if is_fill_row(r):
            stats["fills"] += 1
        elif is_skip_row(r):
            stats["skips"] += 1

    mode = (mode or "trades").lower()
    if mode == "trades":
        lines = [r for r in rows if is_fill_row(r)][:limit]
        # Never fall back to skip rows when there are zero fills.
    else:
        lines = rows[:limit]

    sample = rows[0] if rows else {}
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "bot": bot_key,
        "mode": mode,
        "lines": lines,
        "stats": stats,
        "source_file": path.name if path else None,
        "elapsed_ms": elapsed_ms,
        "seat_label": live_seat_label(bot_key, sample),
        "running_label": "RUNNING · " + (str(sample.get("mode") or sample.get("ledger") or "paper").lower()),
    }
