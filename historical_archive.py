"""Preserve pre-capital-launch logs for backtest — active logs may reset, archives never deleted."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot_common import (
    ETH_LIVE_SESSION_FILE,
    ETH_LIVE_TRADE_LOG_FILE,
    ETH_OBSERVATIONS_FILE,
    ETH_STATE_FILE,
    ETH_TRADE_LOG_FILE,
    ETH_WINDOW_STATS_FILE,
    ETH_WINDOWS_FILE,
    LIVE_SESSION_FILE,
    LIVE_TRADE_LOG_FILE,
    LOG_DIR,
    NEW_STATE_FILE,
    OBSERVATIONS_FILE,
    PAPER_TRADE_LOG_FILE,
    STATE_FILE,
    STRATEGY_CONFIG_FILE,
    TRADE_LOG_FILE,
    WINDOW_STATS_FILE,
    WINDOWS_FILE,
)

HKT = ZoneInfo("Asia/Hong_Kong")
BACKTEST_ARCHIVE_ROOT = LOG_DIR / "backtest_archive"
BACKUP_ROOT = Path("/root/backups")
MANIFEST_FILE = BACKTEST_ARCHIVE_ROOT / "manifest.json"

# Cutoff label for the pre-$1000 era (strategy go-live reference date).
PRE_1000_CUTOFF_LABEL = "2026-06-24"

# All paths worth keeping for future backtest / replay.
BACKTEST_PATHS: tuple[Path, ...] = (
    LIVE_TRADE_LOG_FILE,
    ETH_LIVE_TRADE_LOG_FILE,
    PAPER_TRADE_LOG_FILE,
    ETH_TRADE_LOG_FILE,
    TRADE_LOG_FILE,
    LIVE_SESSION_FILE,
    ETH_LIVE_SESSION_FILE,
    LOG_DIR / "eth_paper_session.json",
    WINDOWS_FILE,
    ETH_WINDOWS_FILE,
    WINDOW_STATS_FILE,
    ETH_WINDOW_STATS_FILE,
    OBSERVATIONS_FILE,
    ETH_OBSERVATIONS_FILE,
    NEW_STATE_FILE,
    LOG_DIR / "package2_risk_state.json",
    LOG_DIR / "manual_bets.json",
    LOG_DIR / "manual_bets_sync_state.json",
    STRATEGY_CONFIG_FILE,
    LOG_DIR / "extended_hours_live.json",
    STATE_FILE,
    ETH_STATE_FILE,
    LOG_DIR / "hourly_performance.json",
    LOG_DIR / "capital_launch_state.json",
    LOG_DIR / "backtest_tape.jsonl",
    LOG_DIR / "backtest_tape_state.json",
    LOG_DIR / "session_research_live.json",
)


def _now_hkt() -> str:
    return datetime.now(timezone.utc).astimezone(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def _load_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            bets = data.get("bets")
            if isinstance(bets, list):
                return bets
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _trade_stats(rows: list[dict]) -> dict[str, Any]:
    entered = [r for r in rows if r.get("entered")]
    closed = [r for r in entered if r.get("trade_result") in ("WIN", "LOSS")]
    wins = sum(1 for r in closed if r.get("trade_result") == "WIN")
    pnl = round(sum(float(r.get("pnl") or 0) for r in closed), 2)
    return {
        "rows": len(rows),
        "entered": len(entered),
        "closed": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "wr_pct": round(100.0 * wins / len(closed), 1) if closed else None,
        "pnl": pnl,
    }


def _archive_summary(archive_dir: Path) -> dict[str, Any]:
    btc_live = _load_json_list(archive_dir / "trade_log_live.json")
    eth_live = _load_json_list(archive_dir / "trade_log_eth_live.json")
    btc_paper = _load_json_list(archive_dir / "trade_log_paper.json")
    eth_paper = _load_json_list(archive_dir / "trade_log_eth_paper.json")
    return {
        "btc_live": _trade_stats(btc_live),
        "eth_live": _trade_stats(eth_live),
        "btc_paper": _trade_stats(btc_paper),
        "eth_paper": _trade_stats(eth_paper),
        "manual_bets": len(_load_json_list(archive_dir / "manual_bets.json")),
    }


def _copy_backtest_files(dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src in BACKTEST_PATHS:
        if not src.exists():
            continue
        target = dest / src.name
        shutil.copy2(src, target)
        copied.append(src.name)
    return copied


def _update_manifest(entry: dict[str, Any]) -> dict[str, Any]:
    BACKTEST_ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"archives": [], "updated_at_hkt": _now_hkt()}
    if MANIFEST_FILE.exists():
        try:
            manifest = json.loads(MANIFEST_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    archives: list[dict] = manifest.get("archives") or []
    archives = [a for a in archives if a.get("id") != entry.get("id")]
    archives.append(entry)
    archives.sort(key=lambda a: a.get("archived_at_hkt") or "")
    manifest["archives"] = archives
    manifest["updated_at_hkt"] = _now_hkt()
    manifest["latest_id"] = entry.get("id")
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def archive_pre_capital(
    *,
    archive_id: str | None = None,
    reason: str = "pre-1000 snapshot",
    cutoff_label: str = PRE_1000_CUTOFF_LABEL,
    mirror_backup: bool = True,
) -> dict[str, Any]:
    """
    Copy all backtest-relevant logs to logs/backtest_archive/ (never overwritten on reset).
    Returns archive metadata including paths and trade counts.
    """
    ts = datetime.now(timezone.utc)
    hkt = ts.astimezone(HKT)
    stamp = ts.strftime("%Y%m%d_%H%M%S")
    archive_id = archive_id or f"pre-1000-{cutoff_label.replace('-', '')}-{stamp}"
    archive_dir = BACKTEST_ARCHIVE_ROOT / archive_id
    copied = _copy_backtest_files(archive_dir)

    summary = _archive_summary(archive_dir)
    session_research: dict[str, Any] = {}
    try:
        from backtest_research import build_session_research_from_paths

        session_research = build_session_research_from_paths(
            archive_dir / "trade_log_live.json",
            archive_dir / "trade_log_eth_live.json",
            source=archive_id,
        )
        (archive_dir / "session_research.json").write_text(
            json.dumps(session_research, indent=2) + "\n"
        )
    except Exception as exc:
        session_research = {"error": str(exc)}

    entry = {
        "id": archive_id,
        "reason": reason,
        "cutoff_label": cutoff_label,
        "archived_at_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "archived_at_hkt": hkt.strftime("%Y-%m-%d %H:%M:%S HKT"),
        "archive_path": str(archive_dir),
        "files_copied": copied,
        "trade_summary": summary,
        "session_research": {
            "file": "session_research.json",
            "pause_scenarios_count": len(session_research.get("pause_scenarios") or []),
            "btc_us_fav_pnl": ((session_research.get("btc") or {}).get("us_hours") or {}).get("favorite", {}).get("pnl"),
            "eth_outside_fav_pnl": ((session_research.get("eth") or {}).get("outside_us") or {}).get("favorite", {}).get("pnl"),
        },
        "note": (
            f"Pre-${cutoff_label} era data preserved for backtest. "
            "Active logs may be reset at $1000 capital launch; this archive is permanent."
        ),
    }
    (archive_dir / "archive_manifest.json").write_text(json.dumps(entry, indent=2) + "\n")

    backup_dir: str | None = None
    if mirror_backup:
        backup_dir_path = BACKUP_ROOT / archive_id
        if backup_dir_path != archive_dir:
            _copy_backtest_files(backup_dir_path)
            (backup_dir_path / "archive_manifest.json").write_text(
                json.dumps(entry, indent=2) + "\n"
            )
            backup_dir = str(backup_dir_path)

    entry["backup_path"] = backup_dir
    _update_manifest(entry)

    try:
        from backtest_research import sync_backtest_tape_from_logs, refresh_live_session_research

        sync_backtest_tape_from_logs()
        refresh_live_session_research()
    except Exception:
        pass

    return entry


def list_archives() -> dict[str, Any]:
    if not MANIFEST_FILE.exists():
        return {"archives": [], "updated_at_hkt": None}
    try:
        return json.loads(MANIFEST_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"archives": [], "updated_at_hkt": None}
