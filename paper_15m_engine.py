"""15m crypto observer — separate paper vs live trade logs.

Repo fallback for CI/dev. Production droplets often already ship a fuller
``paper_15m_engine.py`` — do not overwrite that file on deploy (see docs/DEPLOY_DESK.md).
"""

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


def _window_key(row: dict) -> str:
    for key in ("slug", "market_id", "window_id", "condition_id", "market"):
        val = row.get(key)
        if val:
            return str(val)
    ts = str(row.get("timestamp_hkt") or row.get("timestamp_utc") or "")
    bot = str(row.get("bot") or row.get("asset") or "")
    return f"{bot}:{ts}"


def _window_funnel_from_rows(rows: list[dict]) -> dict[str, Any]:
    """Per-window decisions from active ledger log (dedupe by market/window key)."""
    by_key: dict[str, dict] = {}
    for row in rows:
        key = _window_key(row)
        prev = by_key.get(key)
        if prev is None or row.get("entered"):
            by_key[key] = row
    considered = len(by_key)
    entered = sum(1 for r in by_key.values() if r.get("entered"))
    filtered = max(0, considered - entered)
    return {
        "considered": considered,
        "filtered": filtered,
        "entered": entered,
        "source": "session_log",
    }


def _window_funnel_from_quality(quality: dict[str, Any] | None) -> dict[str, Any] | None:
    if not quality:
        return None
    considered = (
        quality.get("windows_considered")
        or quality.get("windows")
        or quality.get("windows_logged")
        or quality.get("strategy_signals")
    )
    entered = quality.get("entered") or quality.get("trades_entered") or quality.get("entered_count")
    filtered = quality.get("filtered") or quality.get("skipped") or quality.get("skipped_count")
    if considered is None and entered is None and filtered is None:
        return None
    if filtered is None and considered is not None and entered is not None:
        filtered = max(0, int(considered) - int(entered))
    return {
        "considered": int(considered) if considered is not None else None,
        "filtered": int(filtered) if filtered is not None else None,
        "entered": int(entered) if entered is not None else None,
        "source": "quality",
    }


def _attach_funnel_trades(funnel: dict[str, Any], trades_entered: int) -> dict[str, Any]:
    """Gold-card line: N 單 (log entered rows) · M 窗 (considered windows)."""
    out = dict(funnel)
    out["trades"] = int(trades_entered or 0)
    return out


def _resolve_window_funnel(
    rows: list[dict],
    quality_combined: dict[str, Any] | None,
    trades_entered_fallback: int,
) -> dict[str, Any]:
    from_log = _window_funnel_from_rows(rows)
    from_quality = _window_funnel_from_quality(quality_combined)
    if from_quality and from_quality.get("considered") is not None:
        out = dict(from_quality)
        if out.get("entered") is None:
            out["entered"] = trades_entered_fallback or from_log["entered"]
        if out.get("filtered") is None and out.get("considered") is not None and out.get("entered") is not None:
            out["filtered"] = max(0, int(out["considered"]) - int(out["entered"]))
        out["source"] = "quality+log"
        return _attach_funnel_trades(out, trades_entered_fallback)
    return _attach_funnel_trades(from_log, trades_entered_fallback)


def enrich_summary_window_funnel(summary: dict[str, Any]) -> dict[str, Any]:
    """Ensure window_funnel exists (droplet engines may only expose quality.combined)."""
    out = dict(summary)
    wf = out.get("window_funnel")
    if isinstance(wf, dict) and wf.get("considered") is not None:
        if wf.get("trades") is None:
            btc = out.get("btc") if isinstance(out.get("btc"), dict) else {}
            eth = out.get("eth") if isinstance(out.get("eth"), dict) else {}
            entered_fb = int(btc.get("trades_entered") or 0) + int(eth.get("trades_entered") or 0)
            wf = dict(wf)
            wf["trades"] = entered_fb
            out["window_funnel"] = wf
        return out
    mode = str(out.get("mode") or crypto_15m_trading_mode()).lower()
    ledger = "live" if mode == "live" else "paper"
    rows = _load_ledger(ledger)
    quality = out.get("quality") if isinstance(out.get("quality"), dict) else {}
    combined = quality.get("combined") if isinstance(quality.get("combined"), dict) else {}
    btc = out.get("btc") if isinstance(out.get("btc"), dict) else {}
    eth = out.get("eth") if isinstance(out.get("eth"), dict) else {}
    entered_fb = int(btc.get("trades_entered") or 0) + int(eth.get("trades_entered") or 0)
    funnel = _attach_funnel_trades(
        _resolve_window_funnel(rows, combined, entered_fb),
        entered_fb,
    )
    funnel["ledger"] = ledger
    out["window_funnel"] = funnel
    return out


def crypto_15m_summary() -> dict[str, Any]:
    mode = crypto_15m_trading_mode()
    active_ledger = "live" if mode == "live" else "paper"
    active_rows = _load_ledger(active_ledger)
    paper_rows = _load_ledger("paper")
    live_rows = _load_ledger("live")
    sess = load_session()
    quality: dict[str, Any] = {"combined": {}, "eth": {}}
    btc = _asset_stats(active_rows, "BTC")
    eth = _asset_stats(active_rows, "ETH")
    trades_entered = int(btc.get("trades_entered") or 0) + int(eth.get("trades_entered") or 0)
    window_funnel = _resolve_window_funnel(
        active_rows,
        quality.get("combined") if isinstance(quality.get("combined"), dict) else {},
        trades_entered,
    )
    window_funnel["ledger"] = active_ledger
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
        "btc": btc,
        "eth": eth,
        "window_funnel": window_funnel,
        "rules": {
            "entry_window": "T-75 → T-22",
            "min_btc_move_usd": 0.8,
            "min_eth_move_usd": 0.8,
            "min_depth_usd": 500,
        },
        "quality": quality,
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
