"""Split Paper and Live PnL / win rate for the three Family Desk gold-box bots.

Skip rows are not wins. Breakeven counts in PnL and is excluded from the win-rate
denominator. Live archives are deduped by market slug so snapshot copies are not
added twice. Parent V2 live history is profile ``v2`` only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

SONU_ID = "son-btc-5m-ultimate-live"
V2_ID = "dual-leg-live-v2"
NA_ID = "son-btc-2legs-v2-newnew-a"
GOLD_IDS = (SONU_ID, V2_ID, NA_ID)

PARENT_LIVE_PROFILES = {"v2", "v3"}
NEWNEW_LIVE_PROFILE = "v2_live_shadow"
SONU_LIVE_UNIT = "btc-son-btc-5m-ultimate-live-exec.service"
SONU_PAPER_UNIT = "btc-son-btc-5m-ultimate-live.service"
V2_LIVE_UNIT = "dual-leg-live-v2.service"
NA_PAPER_UNIT = "btc-son-btc-2legs-v2-newnew-a.service"
NA_LIVE_UNIT = "dual-leg-live-v2-newnew-a.service"
OBSERVER_UNIT = "btc-5m-observer.service"
_SETTLED = {"WIN", "LOSS"}
_BREAKEVEN = {"BREAKEVEN", "BE", "PUSH"}
_GAP = timedelta(hours=6)
_TIME_FIELDS = (
    "logged_at_hkt",
    "opened_at_hkt",
    "settled_at_hkt",
    "closed_at_hkt",
    "timestamp_hkt",
    "time_hkt",
)
_TS_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)")

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


def _now_hkt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)


def _parse_hkt(value: Any) -> datetime | None:
    if not value:
        return None
    match = _TS_RE.search(str(value))
    if not match:
        return None
    text = f"{match.group(1)} {match.group(2)}"
    fmt = "%Y-%m-%d %H:%M:%S" if text.count(":") == 2 else "%Y-%m-%d %H:%M"
    try:
        return datetime.strptime(text, fmt)
    except ValueError:
        return None


def _row_bounds(row: dict[str, Any]) -> tuple[datetime, datetime] | None:
    times = [stamp for field in _TIME_FIELDS if (stamp := _parse_hkt(row.get(field)))]
    if not times:
        return None
    return min(times), max(times)


def _clusters(rows: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    bounds = [bound for row in rows if isinstance(row, dict) and (bound := _row_bounds(row))]
    bounds.sort()
    if not bounds:
        return []
    segments = [bounds[0]]
    for start, end in bounds[1:]:
        prev_start, prev_end = segments[-1]
        if start - prev_end > _GAP:
            segments.append((start, end))
        else:
            segments[-1] = (prev_start, max(prev_end, end))
    return segments


def _dur_label(minutes: int) -> str:
    if minutes <= 0:
        return "少過 1 分"
    days, rem = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}日")
    if hours:
        parts.append(f"{hours}小時")
    if mins and days < 3:
        parts.append(f"{mins}分")
    return "".join(parts) or "少過 1 分"


def _when_label(start: datetime, end: datetime, open_book: bool) -> str:
    left = start.strftime("%m/%d %H:%M")
    if open_book:
        right = "而家"
    elif start.date() == end.date():
        right = end.strftime("%H:%M")
    else:
        right = end.strftime("%m/%d %H:%M")
    return f"{left} → {right}"


def _span(
    rows: list[dict[str, Any]],
    *,
    open_book: bool = False,
    started_at: datetime | None = None,
    empty_live: bool = False,
) -> dict[str, Any]:
    segments = _clusters(rows)
    if started_at is not None:
        if not segments:
            segments = [(started_at, _now_hkt() if open_book else started_at)]
        else:
            first_start, first_end = segments[0]
            if started_at < first_start and first_start - started_at <= _GAP:
                segments[0] = (started_at, first_end)
    if open_book and segments:
        start, end = segments[-1]
        segments[-1] = (start, max(end, _now_hkt()))
    if not segments:
        return {
            "when_label": "今次未開始" if empty_live else "未有日期",
            "duration_label": "—",
            "duration_min": None,
            "started_at": None,
            "ended_at": None,
            "open": False,
        }
    start = segments[0][0]
    end = segments[-1][1]
    minutes = int(round(sum((seg_end - seg_start).total_seconds() for seg_start, seg_end in segments) / 60))
    return {
        "when_label": _when_label(start, end, open_book),
        "duration_label": _dur_label(minutes),
        "duration_min": minutes,
        "started_at": start.strftime("%Y-%m-%d %H:%M"),
        "ended_at": end.strftime("%Y-%m-%d %H:%M"),
        "open": bool(open_book),
    }


def _line(
    scope: str,
    rows: list[dict[str, Any]],
    note: str,
    *,
    open_book: bool = False,
    started_at: datetime | None = None,
    empty_live: bool = False,
) -> dict[str, Any]:
    stats = summarize(rows)
    return {
        "scope": scope,
        "note": note,
        **stats,
        **_span(rows, open_book=open_book, started_at=started_at, empty_live=empty_live),
    }


def _unit_active_since_hkt(unit: str) -> datetime | None:
    """ActiveEnterTimestamp of a running unit, converted to Hong Kong time."""
    if os.environ.get("MODE_BOOKS_NO_SYSTEMD") == "1":
        return None
    try:
        raw = subprocess.check_output(
            ["systemctl", "show", unit, "-p", "ActiveState", "-p", "ActiveEnterTimestamp", "--no-pager"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    state = ""
    stamp = ""
    for line in raw.splitlines():
        if line.startswith("ActiveState="):
            state = line.split("=", 1)[1].strip()
        elif line.startswith("ActiveEnterTimestamp="):
            stamp = line.split("=", 1)[1].strip()
    if state != "active":
        return None
    match = re.search(r"(20\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", stamp)
    if not match:
        return None
    try:
        parsed = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if "UTC" in stamp:
        return parsed + timedelta(hours=8)
    return parsed


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
    return is_live_row(row) and str(row.get("profile") or "") in PARENT_LIVE_PROFILES


def _newnew_shared_live(row: dict[str, Any]) -> bool:
    return is_live_row(row) and str(row.get("profile") or "") == NEWNEW_LIVE_PROFILE


def _v2_paper(row: dict[str, Any]) -> bool:
    if not is_paper_row(row):
        return False
    session = str(row.get("session_id") or "")
    return (not session) or ("v2" in session)


def _live_lines(
    current: list[dict[str, Any]],
    history: list[dict[str, Any]],
    segments: int,
    *,
    current_open: bool = False,
    current_started: datetime | None = None,
    pnl_net_of_fees: bool = False,
) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    current_stats = summarize(current)
    history_stats = summarize(history)
    current_n = current_stats["settled_n"] + current_stats["breakeven"]
    history_n = history_stats["settled_n"] + history_stats["breakeven"]
    if current_n or current_open:
        if current_n:
            be = f" · 打和 {current_stats['breakeven']} 唔計勝率" if current_stats["breakeven"] else ""
            note = f"今次真倉 · {current_stats['wl_label']}{be}"
            if pnl_net_of_fees:
                note += " · PnL 已扣 taker 手續費"
        else:
            note = "今次未有真倉成交"
        lines.append(
            _line("今次", current, note, open_book=current_open, started_at=current_started, empty_live=not current_n)
        )
    else:
        lines.append(_line("今次", [], "今次未開始。Live 掣成功開真倉之後，呢度先會有新時段。", empty_live=True))
    if history_n:
        be = f" · 打和 {history_stats['breakeven']} 唔計勝率" if history_stats["breakeven"] else ""
        lines.append(_line("歷史", history, f"歷史真倉 {segments} 段{be}"))
    return lines


def books_for_bot(root: Path, bot_id: str) -> dict[str, Any]:
    root = Path(root)
    if bot_id == SONU_ID:
        name = "son-btc-5m-ultimate-live_trades.json"
        current = _read_rows(root / "logs" / "sons" / name)
        paper = [row for row in current if is_paper_row(row)]
        live_now = [row for row in current if is_live_row(row)]
        prior = _prior_paper(root, name, paper, is_paper_row)
        live_since = _unit_active_since_hkt(SONU_LIVE_UNIT)
        paper_open = live_since is not None or _unit_active_since_hkt(SONU_PAPER_UNIT) is not None
        paper_lines = [_line("今次", paper, _paper_note(paper, prior=False), open_book=paper_open)]
        if prior:
            paper_lines.append(_line("上次", prior, _paper_note(prior, prior=True)))
        return _pack(
            paper_lines,
            _live_lines(live_now, [], 0, current_open=live_since is not None, current_started=live_since),
        )

    if bot_id == V2_ID:
        paper_path = root / "logs" / "dual_leg_btc_paper_trades.json"
        paper = [row for row in _read_rows(paper_path) if _v2_paper(row)]
        prior = _prior_paper(root, "dual_leg_btc_paper_trades.json", paper, _v2_paper)
        seen: set[str] = set()
        live_now, _ = _take([root / "logs" / "dual_leg_live_trades.json"], _parent_live, seen)
        history, segments = _take(_archive_files(root, "dual_leg_live_trades.json"), _parent_live, seen)
        paper_open = _unit_active_since_hkt(OBSERVER_UNIT) is not None
        paper_lines = [_line("今次", paper, _paper_note(paper, prior=False), open_book=paper_open)]
        if prior:
            paper_lines.append(_line("上次", prior, _paper_note(prior, prior=True)))
        live_since = _unit_active_since_hkt(V2_LIVE_UNIT)
        return _pack(
            paper_lines,
            _live_lines(
                live_now,
                history,
                segments,
                current_open=live_since is not None,
                current_started=live_since,
                pnl_net_of_fees=True,
            ),
        )

    if bot_id == NA_ID:
        name = "son-btc-2legs-v2-newnew-a_trades.json"
        current = _read_rows(root / "logs" / "sons" / name)
        paper = [row for row in current if is_paper_row(row)]
        live_now = [row for row in current if is_live_row(row)]
        seen = {row_key(row) for row in live_now}
        shared, _ = _take([root / "logs" / "dual_leg_live_trades.json"], _newnew_shared_live, seen)
        archived, segments = _take(_archive_files(root, "dual_leg_live_trades.json"), _newnew_shared_live, seen)
        prior = _prior_paper(root, name, paper, is_paper_row)
        paper_since = _unit_active_since_hkt(NA_PAPER_UNIT)
        paper_lines = [
            _line(
                "今次",
                paper,
                _paper_note(paper, prior=False),
                open_book=paper_since is not None,
                started_at=paper_since,
            )
        ]
        if prior:
            paper_lines.append(_line("上次", prior, _paper_note(prior, prior=True)))
        live_since = _unit_active_since_hkt(NA_LIVE_UNIT)
        return _pack(
            paper_lines,
            _live_lines(
                live_now + shared,
                archived,
                segments,
                current_open=live_since is not None,
                current_started=live_since,
                pnl_net_of_fees=True,
            ),
        )

    raise KeyError(bot_id)


def _section(
    title: str,
    lines: list[dict[str, Any]],
    *,
    empty_when: str,
    headline_scope: str | None = None,
) -> dict[str, Any]:
    if headline_scope:
        head = next((line for line in lines if line.get("scope") == headline_scope), None)
        if head is not None:
            return {
                "title": title,
                "when_label": head.get("when_label") or empty_when,
                "duration_label": head.get("duration_label") or "—",
                "open": bool(head.get("open")),
                "rows": lines,
            }
    usable = [line for line in lines if line.get("started_at") and line.get("ended_at")]
    if not usable:
        label = str(lines[0].get("when_label")) if lines else empty_when
        return {"title": title, "when_label": label, "duration_label": "—", "open": False, "rows": lines}
    starts = [datetime.strptime(str(line["started_at"]), "%Y-%m-%d %H:%M") for line in usable]
    ends = [datetime.strptime(str(line["ended_at"]), "%Y-%m-%d %H:%M") for line in usable]
    total = sum(int(line.get("duration_min") or 0) for line in usable)
    open_book = any(bool(line.get("open")) for line in usable)
    return {
        "title": title,
        "when_label": _when_label(min(starts), max(ends), open_book),
        "duration_label": _dur_label(total) if total else "—",
        "open": open_book,
        "rows": lines,
    }


def _pack(paper_lines: list[dict[str, Any]], live_lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "paper": _section("Paper · 紙上", paper_lines, empty_when="未有日期"),
        "live": _section("Live · 真倉", live_lines, empty_when="今次未開始", headline_scope="今次"),
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
        head = next((line for line in lines if str(line.get("scope") or "") == "今次"), lines[0])

        def _settled_n(line: dict[str, Any]) -> int:
            try:
                return int(line.get("settled_n") or 0)
            except (TypeError, ValueError):
                return 0

        head_settled = _settled_n(head)
        # A fresh open session stays at zero. Closed empty books still fall back.
        if head_settled == 0 and not head.get("open"):
            for line in lines:
                if line is head:
                    continue
                settled = _settled_n(line)
                if settled > head_settled:
                    head = line
                    head_settled = settled
        row["pnl"] = head.get("pnl")
        row["pnl_label"] = head.get("pnl_label")
        row["wins"] = head.get("wins")
        row["losses"] = head.get("losses")
        row["settled_n"] = head.get("settled_n")
        row["wr_label"] = head.get("wr_label")
