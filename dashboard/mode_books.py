"""Split Paper and Live PnL / win rate for the three Family Desk gold-box bots.

Skip rows are not wins. Breakeven counts in PnL and is excluded from the win-rate
denominator. Live archives are deduped by market slug so snapshot copies are not
added twice. Parent V2 live history is profile ``v2`` only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

SONU_ID = "son-btc-5m-ultimate-live"
V2_ID = "dual-leg-live-v2"
NA_ID = "son-btc-2legs-v2-newnew-a"
GOLD_IDS = (SONU_ID, V2_ID, NA_ID)

PARENT_LIVE_PROFILE = "v2"
NEWNEW_LIVE_PROFILE = "v2_live_shadow"
_SETTLED = {"WIN", "LOSS"}
_BREAKEVEN = {"BREAKEVEN", "BE", "PUSH"}

_ROW_CACHE: dict[str, tuple[float, int, list[dict[str, Any]]]] = {}


def money(value: float | None) -> str:
    if value is None:
        return "$0.00"
    number = round(float(value), 2)
    if abs(number) < 0.005:
        return "$0.00"
    sign = "+" if number > 0 else "-"
    return f"{sign}${abs(number):.2f}"


def wr_label(wins: int, losses: int) -> str:
    settled = wins + losses
    if not settled:
        return "未有結算"
    pct = 100.0 * wins / settled
    rounded = round(pct)
    if abs(pct - rounded) < 0.05:
        return f"{rounded:.0f}%"
    return f"{pct:.1f}%"


def wl_label(wins: int, losses: int, breakeven: int) -> str:
    text = f"{wins}W/{losses}L"
    if breakeven:
        text += f"/{breakeven}和"
    return text


def mode_of(row: dict[str, Any]) -> str:
    return str(row.get("mode") or row.get("trading_mode") or "").strip().upper()


def result_of(row: dict[str, Any]) -> str:
    return str(row.get("trade_result") or row.get("result") or "").strip().upper()


def is_live_row(row: dict[str, Any]) -> bool:
    return mode_of(row) == "LIVE"


def is_paper_row(row: dict[str, Any]) -> bool:
    return not is_live_row(row)


def row_key(row: dict[str, Any]) -> str:
    slug = str(row.get("slug") or "").strip()
    if slug:
        return slug
    opened = str(row.get("opened_at_hkt") or row.get("logged_at_hkt") or row.get("settled_at_hkt") or "")
    return "|".join([opened, result_of(row), str(row.get("pnl"))])


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = losses = breakeven = skips = 0
    pnl = 0.0
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = row_key(row)
        if key in seen:
            continue
        seen.add(key)
        result = result_of(row)
        if result == "WIN":
            wins += 1
        elif result == "LOSS":
            losses += 1
        elif result in _BREAKEVEN:
            breakeven += 1
        elif result == "SKIP":
            skips += 1
        if result in _SETTLED or result in _BREAKEVEN:
            raw = row.get("pnl")
            if raw is None:
                raw = row.get("pnl_usd")
            try:
                pnl += float(raw or 0)
            except (TypeError, ValueError):
                pass
    return {
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "skips": skips,
        "settled_n": wins + losses,
        "pnl": round(pnl, 2),
        "pnl_label": money(pnl),
        "wr_label": wr_label(wins, losses),
        "wl_label": wl_label(wins, losses, breakeven),
    }


def _line(scope: str, rows: list[dict[str, Any]], note: str) -> dict[str, Any]:
    stats = summarize(rows)
    return {
        "scope": scope,
        "note": note,
        **stats,
    }


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        st = path.stat()
    except OSError:
        return []
    key = str(path)
    hit = _ROW_CACHE.get(key)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = []
    rows = [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []
    _ROW_CACHE[key] = (st.st_mtime, st.st_size, rows)
    return rows


def _archive_files(root: Path, name: str) -> list[Path]:
    base = root / "logs" / "archive"
    if not base.is_dir():
        return []
    found: list[Path] = []
    try:
        paths = list(base.rglob(name))
    except OSError:
        return []
    for path in paths:
        if not path.is_file():
            continue
        if "dual_leg_v1" in path.as_posix():
            continue
        found.append(path)
    found.sort(key=lambda item: item.stat().st_mtime if item.is_file() else 0, reverse=True)
    return found


def _take(files: list[Path], pred: Callable[[dict[str, Any]], bool], seen: set[str]) -> tuple[list[dict[str, Any]], int]:
    chosen: list[dict[str, Any]] = []
    used = 0
    for path in files:
        chunk: list[dict[str, Any]] = []
        for row in _read_rows(path):
            if not pred(row):
                continue
            key = row_key(row)
            if key in seen:
                continue
            seen.add(key)
            chunk.append(row)
        if chunk:
            used += 1
            chosen.extend(chunk)
    return chosen, used


def _paper_note(stats_rows: list[dict[str, Any]], *, prior: bool) -> str:
    stats = summarize(stats_rows)
    head = "上次紙上" if prior else "今次紙上"
    if stats["settled_n"] or stats["breakeven"]:
        extra = f" · {stats['skips']} skip 唔計勝率" if stats["skips"] else ""
        return f"{head} · {stats['wl_label']}{extra}"
    if stats["skips"]:
        return f"{head} · {stats['skips']} 窗 skip · 未有結算"
    return f"{head} · 未有結算"


def _prior_paper(root: Path, filename: str, current: list[dict[str, Any]], pred: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    current_keys = {row_key(row) for row in current}
    for path in _archive_files(root, filename):
        rows = [row for row in _read_rows(path) if pred(row) and row_key(row) not in current_keys]
        stats = summarize(rows)
        if stats["settled_n"] or stats["breakeven"]:
            return rows
    return []


def _parent_live(row: dict[str, Any]) -> bool:
    return is_live_row(row) and str(row.get("profile") or "") == PARENT_LIVE_PROFILE


def _newnew_shared_live(row: dict[str, Any]) -> bool:
    return is_live_row(row) and str(row.get("profile") or "") == NEWNEW_LIVE_PROFILE


def _v2_paper(row: dict[str, Any]) -> bool:
    if not is_paper_row(row):
        return False
    session = str(row.get("session_id") or "")
    return (not session) or ("v2" in session)


def _live_lines(current: list[dict[str, Any]], history: list[dict[str, Any]], segments: int) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    current_stats = summarize(current)
    history_stats = summarize(history)
    current_n = current_stats["settled_n"] + current_stats["breakeven"]
    history_n = history_stats["settled_n"] + history_stats["breakeven"]
    if current_n:
        be = f" · 打和 {current_stats['breakeven']} 唔計勝率" if current_stats["breakeven"] else ""
        lines.append(_line("今次", current, f"今次真倉 · {current_stats['wl_label']}{be}"))
    if history_n:
        be = f" · 打和 {history_stats['breakeven']} 唔計勝率" if history_stats["breakeven"] else ""
        now = " · 今次帳 0 單" if not current_n else ""
        lines.append(_line("歷史", history, f"歷史真倉 {segments} 段{now}{be}"))
    if not lines:
        lines.append(_line("今次", [], "今次未有真倉成交"))
    return lines


def books_for_bot(root: Path, bot_id: str) -> dict[str, Any]:
    root = Path(root)
    if bot_id == SONU_ID:
        name = "son-btc-5m-ultimate-live_trades.json"
        current = _read_rows(root / "logs" / "sons" / name)
        paper = [row for row in current if is_paper_row(row)]
        live_now = [row for row in current if is_live_row(row)]
        prior = _prior_paper(root, name, paper, is_paper_row)
        paper_lines = [_line("今次", paper, _paper_note(paper, prior=False))]
        if prior:
            paper_lines.append(_line("上次", prior, _paper_note(prior, prior=True)))
        return _pack(paper_lines, _live_lines(live_now, [], 0))

    if bot_id == V2_ID:
        paper_path = root / "logs" / "dual_leg_btc_paper_trades.json"
        paper = [row for row in _read_rows(paper_path) if _v2_paper(row)]
        prior = _prior_paper(root, "dual_leg_btc_paper_trades.json", paper, _v2_paper)
        seen: set[str] = set()
        live_now, _ = _take([root / "logs" / "dual_leg_live_trades.json"], _parent_live, seen)
        history, segments = _take(_archive_files(root, "dual_leg_live_trades.json"), _parent_live, seen)
        paper_lines = [_line("今次", paper, _paper_note(paper, prior=False))]
        if prior:
            paper_lines.append(_line("上次", prior, _paper_note(prior, prior=True)))
        return _pack(paper_lines, _live_lines(live_now, history, segments))

    if bot_id == NA_ID:
        name = "son-btc-2legs-v2-newnew-a_trades.json"
        current = _read_rows(root / "logs" / "sons" / name)
        paper = [row for row in current if is_paper_row(row)]
        live_now = [row for row in current if is_live_row(row)]
        seen = {row_key(row) for row in live_now}
        shared, _ = _take([root / "logs" / "dual_leg_live_trades.json"], _newnew_shared_live, seen)
        archived, _ = _take(_archive_files(root, "dual_leg_live_trades.json"), _newnew_shared_live, seen)
        prior = _prior_paper(root, name, paper, is_paper_row)
        paper_lines = [_line("今次", paper, _paper_note(paper, prior=False))]
        if prior:
            paper_lines.append(_line("上次", prior, _paper_note(prior, prior=True)))
        return _pack(paper_lines, _live_lines(live_now + shared, archived, _))

    raise KeyError(bot_id)


def _pack(paper_lines: list[dict[str, Any]], live_lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "paper": {"title": "Paper · 紙上", "rows": paper_lines},
        "live": {"title": "Live · 真倉", "rows": live_lines},
    }


def attach_mode_books(root: Path, rows: list[dict[str, Any]]) -> None:
    """Attach ``mode_books`` and point the card headline at the active mode's 今次 line."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        bot_id = str(row.get("id") or "")
        if bot_id not in GOLD_IDS:
            continue
        try:
            books = books_for_bot(root, bot_id)
        except Exception:
            continue
        row["mode_books"] = books
        active = "live" if row.get("live_enabled") else "paper"
        lines = ((books.get(active) or {}).get("rows") or [])
        if not lines:
            continue
        head = lines[0]
        row["pnl"] = head.get("pnl")
        row["pnl_label"] = head.get("pnl_label")
        row["wins"] = head.get("wins")
        row["losses"] = head.get("losses")
        row["settled_n"] = head.get("settled_n")
        row["wr_label"] = head.get("wr_label")
