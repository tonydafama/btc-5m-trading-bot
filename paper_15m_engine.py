"""15m crypto observer — separate paper vs live trade logs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(os.getenv("BTC_5M_ROOT", str(Path(__file__).resolve().parent)))


def _root() -> Path:
    return Path(os.getenv("BTC_5M_ROOT", str(Path(__file__).resolve().parent)))


def _log_dir() -> Path:
    return _root() / "logs"


def _paper_log_path() -> Path:
    return _log_dir() / "crypto_15m_trade_log_paper.json"


def _live_log_path() -> Path:
    return _log_dir() / "crypto_15m_trade_log_live.json"


def _session_path() -> Path:
    return _log_dir() / "crypto_15m_session.json"


GO_LIVE_MIN_WALLET_USD = float(os.getenv("CRYPTO_15M_MIN_WALLET_USD", "50"))


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def crypto_15m_trading_mode() -> str:
    return os.getenv("CRYPTO_15M_TRADING_MODE", "paper").lower()


def crypto_15m_paused() -> bool:
    try:
        from bot_control import load_bot_control

        return bool((load_bot_control().get("crypto_15m") or {}).get("paused"))
    except Exception:
        return False


def load_session() -> dict[str, Any]:
    return _load_json(_session_path(), {})


def _load_ledger(ledger: str) -> list[dict]:
    ledger = (ledger or "paper").lower()
    path = _live_log_path() if ledger == "live" else _paper_log_path()
    rows = _load_json(path, [])
    return rows if isinstance(rows, list) else []


def _asset_stats(rows: list[dict], bot: str) -> dict[str, Any]:
    subset = [r for r in rows if str(r.get("bot") or r.get("asset") or "").upper().startswith(bot)]
    entered = [r for r in subset if r.get("entered")]
    closed = [r for r in entered if r.get("trade_result") in ("WIN", "LOSS")]
    wins = sum(1 for r in closed if r.get("trade_result") == "WIN")
    pnl = round(sum(float(r.get("pnl") or 0) for r in closed), 2)
    wr = round(100.0 * wins / len(closed), 1) if closed else None
    return {
        "overall_pnl": pnl,
        "trades_entered": len(entered),
        "trades_closed": len(closed),
        "win_rate_pct": wr,
    }


def _combined_pnl(rows: list[dict]) -> float:
    closed = [
        r
        for r in rows
        if r.get("entered") and r.get("trade_result") in ("WIN", "LOSS")
    ]
    return round(sum(float(r.get("pnl") or 0) for r in closed), 2)


def crypto_15m_summary() -> dict[str, Any]:
    mode = crypto_15m_trading_mode()
    active_ledger = "live" if mode == "live" else "paper"
    active_rows = _load_ledger(active_ledger)
    paper_rows = _load_ledger("paper")
    live_rows = _load_ledger("live")
    sess = load_session()
    return {
        "mode": mode,
        "paused": crypto_15m_paused(),
        "data_collection": os.getenv("CRYPTO_15M_DATA_COLLECTION", "1") == "1" and mode != "live",
        "paper_fill_model": "live_style_fok",
        "paper_max_ask_favorite": 0.92,
        "session": sess,
        "combined_overall_pnl": _combined_pnl(active_rows),
        "combined_paper_pnl": _combined_pnl(paper_rows),
        "combined_live_pnl": _combined_pnl(live_rows),
        "btc": _asset_stats(active_rows, "BTC"),
        "eth": _asset_stats(active_rows, "ETH"),
        "rules": {
            "entry_window": "T-75 → T-22",
            "min_btc_move_usd": 0.8,
            "min_eth_move_usd": 0.8,
            "min_depth_usd": 500,
        },
        "quality": {"combined": {}, "eth": {}},
    }


def crypto_15m_quality_report() -> dict[str, Any]:
    return {"combined": {}, "eth": {}, "btc": {}}


def go_live_wallet_gate() -> dict[str, Any]:
    cash: float | None = None
    try:
        from new_strategy import fetch_wallet_cash

        cash = float(fetch_wallet_cash())
    except Exception:
        cash = None
    ok = cash is not None and cash >= GO_LIVE_MIN_WALLET_USD
    return {
        "min_wallet_usd": GO_LIVE_MIN_WALLET_USD,
        "wallet_usd": cash,
        "ok": ok,
        "message": (
            f"Wallet ≥ ${GO_LIVE_MIN_WALLET_USD:.0f} 先可 Live"
            if not ok
            else "Wallet OK · 可按 Live 上線"
        ),
    }


def recent_trades(
    *,
    limit: int = 30,
    ledger: str | None = None,
    entered_only: bool = False,
) -> list[dict]:
    mode = crypto_15m_trading_mode()
    if ledger is None:
        ledger = "live" if mode == "live" else "paper"
    ledger = ledger.lower()
    rows = _load_ledger(ledger)
    for r in rows:
        r.setdefault("mode", ledger)
    if entered_only:
        rows = [r for r in rows if r.get("entered")]
    rows = sorted(
        rows,
        key=lambda r: str(r.get("timestamp_hkt") or r.get("timestamp_utc") or ""),
        reverse=True,
    )
    return rows[:limit]
