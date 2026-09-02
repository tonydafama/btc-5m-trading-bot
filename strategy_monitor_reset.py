"""Reset V1 + Alpha-Max for Strategy Monitor competition while archiving all data."""

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
    LIVE_SESSION_FILE,
    LIVE_TRADE_LOG_FILE,
    LOG_DIR,
    NEW_STATE_FILE,
)

HKT = ZoneInfo("Asia/Hong_Kong")
ROOT = Path("/root/btc-5m-bot")
CONFIG_FILE = LOG_DIR / "competition_config.json"
BACKTEST_ARCHIVE_ROOT = LOG_DIR / "backtest_archive"
MANIFEST_FILE = BACKTEST_ARCHIVE_ROOT / "manifest.json"

BOT2_DIR = LOG_DIR / "bot2"
ALPHA_LIVE_LOG = BOT2_DIR / "alpha_max_trades_live.json"
ALPHA_LIVE_SESSION = BOT2_DIR / "alpha_max_live_session.json"
ALPHA_LIVE_STATE = BOT2_DIR / "alpha_max_live_state.json"

ARCHIVE_PATHS: tuple[Path, ...] = (
    LIVE_TRADE_LOG_FILE,
    ETH_LIVE_TRADE_LOG_FILE,
    LIVE_SESSION_FILE,
    ETH_LIVE_SESSION_FILE,
    NEW_STATE_FILE,
    LOG_DIR / "wallet_cache.json",
    LOG_DIR / "manual_bets.json",
    LOG_DIR / "manual_bets_sync_state.json",
    LOG_DIR / "package2_risk_state.json",
    LOG_DIR / "session_research_live.json",
    CONFIG_FILE,
    ALPHA_LIVE_LOG,
    ALPHA_LIVE_SESSION,
    ALPHA_LIVE_STATE,
    BOT2_DIR / "wallet_cache.json",
    LOG_DIR / "alpha_max_trades.json",
    LOG_DIR / "alpha_max_research.jsonl",
    LOG_DIR / "alpha_max_research_summary.json",
)


def _now() -> tuple[str, str, str]:
    utc = datetime.now(timezone.utc)
    hkt = utc.astimezone(HKT)
    stamp = utc.strftime("%Y%m%d-%H%M%S")
    return stamp, utc.strftime("%Y-%m-%dT%H:%M:%SZ"), hkt.strftime("%Y-%m-%d %H:%M:%S HKT")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _wallet_cash(env_file: Path) -> float:
    cache = LOG_DIR / "wallet_cache.json" if env_file.name == ".env" else BOT2_DIR / "wallet_cache.json"
    try:
        import os
        import subprocess

        script = """
import os
from dotenv import load_dotenv
load_dotenv(os.environ['BOT_ENV_FILE'], override=True)
os.environ['WALLET_CACHE_FILE'] = os.environ['WALLET_CACHE_FILE_TARGET']
from polymarket_wallet import fetch_wallet_status
import json
print(json.dumps(fetch_wallet_status(force=True)))
"""
        env = os.environ.copy()
        env["BOT_ENV_FILE"] = str(env_file)
        env["WALLET_CACHE_FILE_TARGET"] = str(cache)
        proc = subprocess.run(
            [str(ROOT / "venv" / "bin" / "python"), "-c", script],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            return round(float(data.get("total_equity") or data.get("cash_usdc") or 0), 2)
    except Exception:
        pass
    data = _load_json(cache, {})
    return round(float(data.get("total_equity") or data.get("cash_usdc") or 0), 2)


def _copy_archive_files(dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src in ARCHIVE_PATHS:
        if not src.exists():
            continue
        rel = src.relative_to(LOG_DIR)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied.append(str(rel))
    return copied


def _update_manifest(entry: dict[str, Any]) -> None:
    BACKTEST_ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"archives": [], "updated_at_hkt": entry.get("archived_at_hkt")}
    if MANIFEST_FILE.exists():
        try:
            manifest = json.loads(MANIFEST_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    archives = [a for a in (manifest.get("archives") or []) if a.get("id") != entry.get("id")]
    archives.append(entry)
    archives.sort(key=lambda a: a.get("archived_at_hkt") or "")
    manifest["archives"] = archives
    manifest["updated_at_hkt"] = entry.get("archived_at_hkt")
    manifest["latest_id"] = entry.get("id")
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n")


def execute_strategy_monitor_reset(
    *,
    v1_baseline_usdc: float = 1000.0,
    alpha_baseline_usdc: float = 1000.0,
) -> dict[str, Any]:
    """Archive all live/monitor data and start fresh V1 vs Alpha-Max competition."""
    stamp, started_utc, started_hkt = _now()
    archive_id = f"pre-competition-{stamp}"
    archive_dir = BACKTEST_ARCHIVE_ROOT / archive_id
    copied = _copy_archive_files(archive_dir)

    v1_wallet = _wallet_cash(ROOT / ".env")
    alpha_wallet = _wallet_cash(ROOT / ".env.bot2")

    prev_config = _load_json(CONFIG_FILE, {})
    btc_session_id = f"v1-competition-{stamp}"
    eth_session_id = f"eth-competition-{stamp}"
    alpha_session_id = f"alpha-competition-{stamp}"

    LIVE_TRADE_LOG_FILE.write_text("[]\n")
    ETH_LIVE_TRADE_LOG_FILE.write_text("[]\n")
    ALPHA_LIVE_LOG.write_text("[]\n")

    btc_session = {
        "session_id": btc_session_id,
        "started_at_utc": started_utc,
        "started_at_hkt": started_hkt,
        "strategy": "PACKAGE2+",
        "trading_mode": "live",
        "dynamic_bankroll": False,
        "starting_wallet_usdc": v1_wallet,
        "competition_baseline_usdc": round(v1_baseline_usdc, 2),
        "wallet_at_reset_usdc": v1_wallet,
        "strategy_monitor_reset": True,
        "archive_path": str(archive_dir),
        "backtest_archive_id": archive_id,
        "note": f"Strategy Monitor competition reset · account start ${v1_wallet:.2f} (no reallocation)",
    }
    eth_session = {
        "session_id": eth_session_id,
        "started_at_utc": started_utc,
        "started_at_hkt": started_hkt,
        "strategy": "PACKAGE2+",
        "trading_mode": "live",
        "starting_wallet_usdc": v1_wallet,
        "starting_bankroll": v1_wallet,
        "competition_baseline_usdc": round(v1_baseline_usdc, 2),
        "wallet_at_reset_usdc": v1_wallet,
        "strategy_monitor_reset": True,
        "archive_path": str(archive_dir),
        "backtest_archive_id": archive_id,
        "note": btc_session["note"],
    }
    alpha_session = {
        "session_id": alpha_session_id,
        "started_at_utc": started_utc,
        "started_at_hkt": started_hkt,
        "strategy": "Alpha-Max Hour Cells",
        "mode": "live",
        "starting_wallet_usdc": alpha_wallet,
        "competition_baseline_usdc": round(alpha_baseline_usdc, 2),
        "wallet_at_reset_usdc": alpha_wallet,
        "strategy_monitor_reset": True,
        "bot_instance": "bot2",
        "archive_path": str(archive_dir),
        "backtest_archive_id": archive_id,
        "note": f"Strategy Monitor competition reset · account start ${alpha_wallet:.2f} (no reallocation)",
    }

    LIVE_SESSION_FILE.write_text(json.dumps(btc_session, indent=2) + "\n")
    ETH_LIVE_SESSION_FILE.write_text(json.dumps(eth_session, indent=2) + "\n")
    ALPHA_LIVE_SESSION.write_text(json.dumps(alpha_session, indent=2) + "\n")
    ALPHA_LIVE_STATE.write_text(
        json.dumps(
            {"wins": 0, "losses": 0, "total_pnl": 0.0, "trades_entered": 0, "trades_skipped": 0},
            indent=2,
        )
        + "\n"
    )

    state = _load_json(NEW_STATE_FILE, {})
    state.update(
        {
            "strategy": "PACKAGE2+",
            "dynamic_bankroll": False,
            "wallet_cash": v1_wallet,
            "bankroll": v1_wallet,
            "trades_entered": 0,
            "trades_skipped": 0,
            "live_session_id": btc_session_id,
        }
    )
    NEW_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")

    try:
        from manual_bets_sync import reset_manual_bets_for_capital_launch

        manual_reset = reset_manual_bets_for_capital_launch(started_hkt)
    except Exception as exc:
        manual_reset = {"error": str(exc)}

    competition_config = {
        "enabled": True,
        "started_at_hkt": f"{started_hkt} HKT" if not str(started_hkt).endswith("HKT") else started_hkt,
        "baseline_usdc": {
            "v1": round(v1_baseline_usdc, 2),
            "alpha_max": round(alpha_baseline_usdc, 2),
            "vault": float((prev_config.get("baseline_usdc") or {}).get("vault") or 3000.0),
            "unallocated": float((prev_config.get("baseline_usdc") or {}).get("unallocated") or 1000.0),
        },
        "accounts": prev_config.get("accounts") or {
            "v1": {"label": "V1", "strategy": "Package2+", "logs": ["trade_log_live.json", "trade_log_eth_live.json"]},
            "alpha_max": {"label": "Alpha Max", "strategy": "Alpha-Max", "logs": ["bot2/alpha_max_trades_live.json"]},
            "vault": {"label": "Vault", "strategy": "Savings only"},
            "unallocated": {"label": "Unallocated", "strategy": "Hold / decide later"},
        },
        "exclude_before_start": True,
        "exclude_manual_trades": True,
        "reset_archive_id": archive_id,
        "account_start_usdc": {"v1": v1_wallet, "alpha_max": alpha_wallet},
        "wallet_at_reset_usdc": {"v1": v1_wallet, "alpha_max": alpha_wallet},
        "notes": [
            "Strategy Monitor competition reset — all prior live logs archived for backtest.",
            f"Account PnL starts at $0 using wallet at reset (V1 ${v1_wallet:.2f}, Alpha ${alpha_wallet:.2f}).",
            "baseline_usdc $1,000 is allocation reference only; no funds moved.",
            "Trade log PnL starts at 0 from monitor start.",
        ],
    }
    CONFIG_FILE.write_text(json.dumps(competition_config, indent=2) + "\n")

    entry = {
        "id": archive_id,
        "reason": "strategy monitor competition reset",
        "archived_at_utc": started_utc,
        "archived_at_hkt": started_hkt,
        "archive_path": str(archive_dir),
        "files_copied": copied,
        "v1_wallet_at_reset": v1_wallet,
        "alpha_wallet_at_reset": alpha_wallet,
        "v1_baseline_usdc": v1_baseline_usdc,
        "alpha_baseline_usdc": alpha_baseline_usdc,
        "btc_session_id": btc_session_id,
        "eth_session_id": eth_session_id,
        "alpha_session_id": alpha_session_id,
        "previous_competition_start": prev_config.get("started_at_hkt"),
        "note": "Permanent backtest archive; active logs cleared for V1 vs Alpha-Max competition.",
    }
    (archive_dir / "archive_manifest.json").write_text(json.dumps(entry, indent=2) + "\n")
    _update_manifest(entry)

    return {
        "ok": True,
        "archive_id": archive_id,
        "archive_path": str(archive_dir),
        "started_at_hkt": started_hkt,
        "v1_wallet_usdc": v1_wallet,
        "alpha_wallet_usdc": alpha_wallet,
        "v1_baseline_usdc": v1_baseline_usdc,
        "alpha_baseline_usdc": alpha_baseline_usdc,
        "btc_session_id": btc_session_id,
        "eth_session_id": eth_session_id,
        "alpha_session_id": alpha_session_id,
        "files_archived": len(copied),
        "manual_bets_reset": manual_reset,
    }
