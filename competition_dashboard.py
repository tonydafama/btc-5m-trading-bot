"""V1 vs Alpha-Max competition dashboard aggregation.

This module intentionally reads fixed log paths instead of relying on
BOT_INSTANCE so the main dashboard cannot mix V1 and Bot #2 data.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path("/root/btc-5m-bot")
LOG_DIR = ROOT / "logs"
CONFIG_FILE = LOG_DIR / "competition_config.json"
BACKTEST_ARCHIVE_ROOT = LOG_DIR / "backtest_archive"
HKT = ZoneInfo("Asia/Hong_Kong")
VENV_PY = ROOT / "venv" / "bin" / "python"
WALLET_REFRESH_TTL_SEC = 90.0
_wallet_refresh_ts: dict[str, float] = {}

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "started_at_hkt": None,
    "baseline_usdc": {
        "v1": 1000.0,
        "alpha_max": 1000.0,
        "vault": 3000.0,
        "unallocated": 1000.0,
    },
    "exclude_before_start": True,
    "exclude_manual_trades": True,
}


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _load_config() -> dict[str, Any]:
    data = _load_json(CONFIG_FILE, {})
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(data if isinstance(data, dict) else {})
    cfg["baseline_usdc"] = {
        **DEFAULT_CONFIG["baseline_usdc"],
        **(data.get("baseline_usdc") if isinstance(data, dict) and isinstance(data.get("baseline_usdc"), dict) else {}),
    }
    return cfg


def _parse_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if text.endswith(" HKT"):
        text = text[:-4].strip()
    try:
        if "T" in text:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(HKT)
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=HKT)
    except ValueError:
        return None


def _row_time(row: dict[str, Any]) -> datetime | None:
    return _parse_time(row.get("timestamp_hkt")) or _parse_time(row.get("timestamp_utc"))


def _fmt_hkt(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def _rows_since(path: Path, start: datetime | None) -> list[dict[str, Any]]:
    rows = _load_json(path, [])
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dt = _row_time(row)
        if start and dt and dt < start:
            continue
        if start and not dt:
            continue
        out.append(row)
    return out


def _wallet(path: Path) -> dict[str, Any]:
    data = _load_json(path, {})
    if not isinstance(data, dict):
        data = {}
    cash = float(data.get("cash_usdc") or 0)
    portfolio = float(data.get("portfolio_value") or 0)
    total = float(data.get("total_equity") or (cash + portfolio))
    return {
        "cash_usdc": round(cash, 2),
        "portfolio_value": round(portfolio, 2),
        "total_equity": round(total, 2),
        "api_ready": bool(data.get("api_ready")),
        "positions_open": int(data.get("positions_open") or 0),
        "updated_utc": data.get("updated_utc"),
        "stale": bool(data.get("stale")),
        "funder": data.get("funder"),
        "deposit_address": data.get("deposit_address"),
    }


def _wallet_trusted_for_pnl(wallet: dict[str, Any]) -> bool:
    """Avoid treating a failed refresh (equity $0, api_ready false) as a total loss."""
    if not wallet.get("updated_utc"):
        return False
    if wallet.get("api_ready"):
        return True
    equity = float(wallet.get("total_equity") or wallet.get("cash_usdc") or 0)
    return equity > 0


def refresh_competition_wallets() -> None:
    """Refresh V1 + Alpha wallet cache files (slow — run in background, not per dashboard poll)."""
    _refresh_wallet_cache(key="v1", env_file=ROOT / ".env", cache_file=LOG_DIR / "wallet_cache.json")
    _refresh_wallet_cache(
        key="alpha_max",
        env_file=ROOT / ".env.bot2",
        cache_file=LOG_DIR / "bot2" / "wallet_cache.json",
    )


def _refresh_wallet_cache(*, key: str, env_file: Path, cache_file: Path) -> None:
    """Refresh wallet cache in an isolated process so envs never cross-contaminate."""
    now = time.time()
    if now - float(_wallet_refresh_ts.get(key, 0.0)) < WALLET_REFRESH_TTL_SEC:
        return
    script = """
import os
from dotenv import load_dotenv
load_dotenv(os.environ['BOT_ENV_FILE'], override=True)
os.environ['WALLET_CACHE_FILE'] = os.environ['WALLET_CACHE_FILE_TARGET']
from polymarket_wallet import fetch_wallet_status
fetch_wallet_status(force=True)
"""
    env = os.environ.copy()
    env["BOT_ENV_FILE"] = str(env_file)
    env["WALLET_CACHE_FILE_TARGET"] = str(cache_file)
    try:
        subprocess.run(
            [str(VENV_PY), "-c", script],
            cwd=str(ROOT),
            env=env,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=18,
        )
        _wallet_refresh_ts[key] = time.time()
    except (OSError, subprocess.TimeoutExpired):
        # Keep showing the last cache; the warning/stale fields make failures visible.
        return


def _today_hkt() -> str:
    return datetime.now(timezone.utc).astimezone(HKT).strftime("%Y-%m-%d")


def _normalize_trade(row: dict[str, Any], *, account: str, bot: str) -> dict[str, Any]:
    result = str(row.get("trade_result") or row.get("status") or "UNKNOWN").upper()
    dt = _row_time(row)
    entered = bool(row.get("entered")) or result in ("WIN", "LOSS", "PENDING")
    return {
        "account": account,
        "bot": bot,
        "asset": row.get("asset"),
        "timestamp_hkt": _fmt_hkt(dt) or str(row.get("timestamp_hkt") or ""),
        "market": row.get("market") or row.get("title") or row.get("slug") or "",
        "slug": row.get("slug"),
        "side": row.get("side") or row.get("bought_contract"),
        "bought_label": row.get("bought_label"),
        "entry_ask": row.get("entry_ask"),
        "tier": row.get("tier"),
        "period": row.get("period"),
        "hour_et": row.get("hour_et"),
        "stake": round(float(row.get("stake") or 0), 2),
        "result": result,
        "trade_result": result,
        "pnl": round(float(row.get("pnl") or 0), 2),
        "entered": entered,
        "status": row.get("status"),
        "strategy_reason": row.get("strategy_reason") or "",
        "skip_category": row.get("skip_category"),
    }


def _enrich_alpha_skip_row(row: dict[str, Any]) -> dict[str, Any]:
    """Fill skip reason / ask from windows or observations when finalize had no checkpoints."""
    try:
        from alpha_max_live import enrich_alpha_skip_display

        return enrich_alpha_skip_display(row, log_dir=LOG_DIR / "bot2")
    except Exception:
        return row


def _include_in_monitor_recent(row: dict[str, Any]) -> bool:
    """Monitor table: real entries / order attempts only — hide SKIPPED window backfill noise."""
    if bool(row.get("entered")):
        return True
    result = str(row.get("trade_result") or row.get("status") or "").upper()
    return result in ("WIN", "LOSS", "PENDING", "FAILED", "NO_FILL")


def _monitor_recent_trades(
    v1_btc: list[dict[str, Any]],
    v1_eth: list[dict[str, Any]],
    alpha_rows: list[dict[str, Any]],
    *,
    limit: int = 25,
) -> list[dict[str, Any]]:
    recent: list[dict[str, Any]] = []
    for row in v1_btc:
        if _include_in_monitor_recent(row):
            recent.append(_normalize_trade(row, account="V1", bot="BTC V1"))
    for row in v1_eth:
        if _include_in_monitor_recent(row):
            recent.append(_normalize_trade(row, account="V1", bot="ETH V1"))
    for row in alpha_rows:
        if _include_in_monitor_recent(row):
            asset = str(row.get("asset") or "Alpha").upper()
            recent.append(_normalize_trade(row, account="Alpha Max", bot=f"Alpha {asset}"))
    recent.sort(
        key=lambda r: _parse_time(r.get("timestamp_hkt")) or datetime.min.replace(tzinfo=HKT),
        reverse=True,
    )
    return recent[:limit]


def _trade_gross_pnl(row: dict[str, Any]) -> float:
    if row.get("gross_pnl") is not None:
        return float(row["gross_pnl"])
    return float(row.get("pnl") or 0)


def _trade_net_pnl(row: dict[str, Any]) -> float:
    gross = _trade_gross_pnl(row)
    fee = _est_entry_fee(row) if row.get("est_fee_usd") is None else float(row["est_fee_usd"])
    if row.get("gross_pnl") is not None:
        return round(float(row.get("pnl") or gross - fee), 2)
    return round(gross - fee, 2)


def _pnl_with_fees(closed_rows: list[dict[str, Any]]) -> dict[str, float]:
    gross = round(sum(_trade_gross_pnl(r) for r in closed_rows), 2)
    fees = round(
        sum(float(r["est_fee_usd"]) if r.get("est_fee_usd") is not None else _est_entry_fee(r) for r in closed_rows),
        2,
    )
    return {"gross_pnl": gross, "estimated_fees_usd": fees, "net_pnl": round(gross - fees, 2)}


def _summarize_bot(
    rows: list[dict[str, Any]], *, account: str, bot: str, fee_adjusted: bool = False
) -> dict[str, Any]:
    closed = [r for r in rows if str(r.get("trade_result") or "").upper() in ("WIN", "LOSS")]
    wins = [r for r in closed if str(r.get("trade_result") or "").upper() == "WIN"]
    entered = [
        r
        for r in rows
        if bool(r.get("entered")) or str(r.get("trade_result") or "").upper() in ("WIN", "LOSS", "PENDING")
    ]
    pending = [
        r
        for r in rows
        if str(r.get("trade_result") or "").upper() == "PENDING" or str(r.get("status") or "").lower() == "open"
    ]
    today = _today_hkt()
    today_closed = [r for r in closed if str(r.get("timestamp_hkt") or "").startswith(today)]
    if fee_adjusted:
        pnl = _pnl_with_fees(closed)["net_pnl"]
        daily = _pnl_with_fees(today_closed)["net_pnl"]
    else:
        pnl = round(sum(float(r.get("pnl") or 0) for r in closed), 2)
        daily = round(sum(float(r.get("pnl") or 0) for r in today_closed), 2)
    return {
        "account": account,
        "bot": bot,
        "rows": len(rows),
        "entered": len(entered),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "realized_pnl": pnl,
        "daily_pnl_hkt": daily,
        "open_positions": len(pending),
    }


def _equity_curve(trades: list[dict[str, Any]], *, fee_adjusted: bool = False) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    total = 0.0
    closed = [r for r in trades if str(r.get("trade_result") or "").upper() in ("WIN", "LOSS")]
    closed.sort(key=lambda r: _row_time(r) or datetime.min.replace(tzinfo=HKT))
    for row in closed:
        delta = _trade_net_pnl(row) if fee_adjusted else float(row.get("pnl") or 0)
        total = round(total + delta, 2)
        points.append({"timestamp_hkt": _fmt_hkt(_row_time(row)), "pnl": total})
    return points


def _manifest_wallet_at_reset(key: str) -> float | None:
    """Historical wallet at auto-reset; fallback only when config/session omit wallet_at_reset."""
    cfg = _load_json(CONFIG_FILE, {})
    archive_id = cfg.get("reset_archive_id")
    if not archive_id:
        return None
    manifest = _load_json(BACKTEST_ARCHIVE_ROOT / archive_id / "archive_manifest.json", {})
    field = "v1_wallet_at_reset" if key == "v1" else "alpha_wallet_at_reset"
    val = manifest.get(field)
    return float(val) if val is not None else None


def _resolve_wallet_at_reset(
    cfg: dict[str, Any],
    key: str,
    *,
    wallet_equity: float = 0.0,
    sess: dict[str, Any] | None = None,
) -> float:
    """Account PnL zero point: config wallet_at_reset → session → manifest → baseline."""
    for src in (
        (cfg.get("wallet_at_reset_usdc") or {}),
        (cfg.get("account_start_usdc") or {}),
    ):
        if isinstance(src, dict) and src.get(key) is not None:
            return float(src[key])
    if sess:
        for field in ("wallet_at_reset_usdc", "starting_wallet_usdc"):
            if sess.get(field) is not None:
                return float(sess[field])
    manifest = _manifest_wallet_at_reset(key)
    if manifest is not None:
        return manifest
    baseline = (cfg.get("baseline_usdc") or {}).get(key)
    if baseline is not None:
        return float(baseline)
    return float(wallet_equity or 0)


def _account_start_usdc(cfg: dict[str, Any], key: str, wallet_equity: float) -> float:
    return _resolve_wallet_at_reset(cfg, key, wallet_equity=wallet_equity)


def _account_summary(
    *,
    key: str,
    label: str,
    baseline: float,
    account_start: float,
    wallet: dict[str, Any],
    bot_summaries: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    min_next_stake: float | None = None,
    fee_adjusted_log: bool = False,
) -> dict[str, Any]:
    closed_trades = [r for r in trades if str(r.get("trade_result") or "").upper() in ("WIN", "LOSS")]
    fee_totals = _pnl_with_fees(closed_trades) if fee_adjusted_log else None
    realized = (
        fee_totals["net_pnl"]
        if fee_totals is not None
        else round(sum(float(b.get("realized_pnl") or 0) for b in bot_summaries), 2)
    )
    daily = round(sum(float(b.get("daily_pnl_hkt") or 0) for b in bot_summaries), 2)
    closed = sum(int(b.get("closed") or 0) for b in bot_summaries)
    wins = sum(int(b.get("wins") or 0) for b in bot_summaries)
    actual_cash = float(wallet.get("cash_usdc") or 0)
    actual_equity = float(wallet.get("total_equity") or actual_cash)
    start = float(account_start or baseline or actual_equity)
    wallet_trusted = _wallet_trusted_for_pnl(wallet)
    if wallet_trusted:
        account_pnl = round(actual_equity - start, 2)
        reconcile_delta = round(account_pnl - realized, 2)
    else:
        account_pnl = None
        reconcile_delta = None
    warnings: list[str] = []
    if not wallet_trusted:
        warnings.append("Wallet unreadable — Account PnL hidden until API responds")
    if reconcile_delta is not None and abs(reconcile_delta) >= 5.0:
        gap_note = "pre-reset activity or untracked fills" if fee_adjusted_log else "fees, rebates, pre-reset activity, or untracked fills"
        warnings.append(f"Wallet vs log gap ${reconcile_delta:+.2f} — {gap_note}")
    baseline_note = (
        f"Allocation reference ${baseline:.0f}; account PnL uses reset wallet ${start:.2f}"
        if baseline and abs(baseline - start) >= 1.0
        else None
    )
    if min_next_stake is not None and actual_cash < min_next_stake:
        warnings.append(f"Cash below next stake ${min_next_stake:.0f}")
    if not wallet.get("api_ready") and key in ("v1", "alpha_max"):
        warnings.append("API not ready")
    if wallet.get("stale"):
        warnings.append("Wallet cache stale")
    return {
        "key": key,
        "label": label,
        "baseline_usdc": round(baseline, 2),
        "account_start_usdc": round(start, 2),
        "actual_equity_usdc": round(actual_equity, 2),
        "actual_cash_usdc": round(actual_cash, 2),
        "account_pnl": account_pnl,
        "account_pnl_available": wallet_trusted,
        "realized_pnl": realized,
        "gross_pnl": fee_totals["gross_pnl"] if fee_totals else None,
        "estimated_fees_usd": fee_totals["estimated_fees_usd"] if fee_totals else None,
        "reconcile_delta": reconcile_delta,
        "competition_baseline_usdc": round(baseline, 2),
        "baseline_note": baseline_note,
        "fair_equity_usdc": round(actual_equity, 2),
        "actual_wallet": wallet,
        "daily_pnl_hkt": daily,
        "roi_pct": round(account_pnl / start * 100, 2) if start and account_pnl is not None else None,
        "log_roi_pct": round(realized / baseline * 100, 2) if baseline else 0.0,
        "closed": closed,
        "wins": wins,
        "losses": closed - wins,
        "win_rate": round(wins / closed * 100, 1) if closed else None,
        "open_positions": sum(int(b.get("open_positions") or 0) for b in bot_summaries),
        "min_next_stake": min_next_stake,
        "warnings": warnings,
        "curve": _equity_curve(trades, fee_adjusted=fee_adjusted_log),
    }


def _est_entry_fee(row: dict[str, Any]) -> float:
    stake = float(row.get("stake") or 0)
    ask = float(row.get("entry_ask") or row.get("fill_price") or 0)
    if ask <= 0 or stake <= 0:
        return 0.0
    shares = stake / ask
    try:
        from polymarket_fees import estimate_taker_fee

        return estimate_taker_fee(shares, ask)
    except Exception:
        return round(shares * 0.07 * ask * (1 - ask), 4)


def _slippage_pnl_impact(row: dict[str, Any]) -> float:
    try:
        from new_strategy import calc_win_pnl
    except Exception:
        return 0.0
    decision = row.get("decision_ask")
    fill = row.get("entry_ask") or row.get("fill_price")
    stake = float(row.get("stake") or 0)
    if decision is None or fill is None or stake <= 0:
        return 0.0
    if row.get("trade_result") != "WIN":
        return 0.0
    return round(calc_win_pnl(stake, float(fill)) - calc_win_pnl(stake, float(decision)), 2)


def _fee_bot(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    closed = [r for r in rows if r.get("entered") and r.get("trade_result") in ("WIN", "LOSS")]
    log_pnl = round(sum(float(r.get("pnl") or 0) for r in closed), 2)
    fees = round(sum(_est_entry_fee(r) for r in closed), 2)
    slips = [_slippage_pnl_impact(r) for r in closed]
    wins = sum(1 for r in closed if r.get("trade_result") == "WIN")
    return {
        "bot": name,
        "trades_closed": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "log_pnl": log_pnl,
        "est_fees": fees,
        "log_pnl_net": round(log_pnl - fees, 2),
        "slippage_in_log": round(sum(slips), 2),
        "fee_pct_of_log": round(fees / log_pnl * 100, 1) if log_pnl else None,
    }


def build_monitor_fee_breakdown() -> dict[str, Any]:
    cfg = _load_config()
    start = _parse_time(cfg.get("started_at_hkt")) if cfg.get("exclude_before_start", True) else None
    baseline = cfg.get("baseline_usdc") or {}

    v1_btc_rows = _rows_since(LOG_DIR / "trade_log_live.json", start)
    v1_eth_rows = _rows_since(LOG_DIR / "trade_log_eth_live.json", start)
    alpha_rows = _rows_since(LOG_DIR / "bot2" / "alpha_max_trades_live.json", start)
    alpha_btc_rows = [r for r in alpha_rows if str(r.get("asset") or "").upper() == "BTC"]
    alpha_eth_rows = [r for r in alpha_rows if str(r.get("asset") or "").upper() == "ETH"]

    btc_v1 = _fee_bot(v1_btc_rows, "BTC V1")
    eth_v1 = _fee_bot(v1_eth_rows, "ETH V1")
    alpha_btc = _fee_bot(alpha_btc_rows, "Alpha BTC")
    alpha_eth = _fee_bot(alpha_eth_rows, "Alpha ETH")
    bots = [btc_v1, eth_v1, alpha_btc, alpha_eth]

    v1_cache = LOG_DIR / "wallet_cache.json"
    alpha_cache = LOG_DIR / "bot2" / "wallet_cache.json"
    _refresh_wallet_cache(key="v1", env_file=ROOT / ".env", cache_file=v1_cache)
    _refresh_wallet_cache(key="alpha_max", env_file=ROOT / ".env.bot2", cache_file=alpha_cache)
    v1_wallet = _wallet(v1_cache)
    alpha_wallet = _wallet(alpha_cache)

    v1_baseline = float(baseline.get("v1") or 1000.0)
    alpha_baseline = float(baseline.get("alpha_max") or 1000.0)
    v1_equity = float(v1_wallet.get("total_equity") or v1_wallet.get("cash_usdc") or 0)
    alpha_equity = float(alpha_wallet.get("total_equity") or alpha_wallet.get("cash_usdc") or 0)
    v1_start = _account_start_usdc(cfg, "v1", v1_equity)
    alpha_start = _account_start_usdc(cfg, "alpha_max", alpha_equity)
    v1_account_pnl = round(v1_equity - v1_start, 2)
    alpha_account_pnl = round(alpha_equity - alpha_start, 2)

    total_log = round(sum(float(b.get("log_pnl") or 0) for b in bots), 2)
    total_fees = round(sum(float(b.get("est_fees") or 0) for b in bots), 2)
    total_slippage = round(sum(float(b.get("slippage_in_log") or 0) for b in bots), 2)
    total_closed = int(sum(int(b.get("trades_closed") or 0) for b in bots))
    combined_account_pnl = round(v1_account_pnl + alpha_account_pnl, 2)
    log_overstatement = round(total_log - combined_account_pnl, 2)

    return {
        "ok": True,
        "scope": "strategy_monitor",
        "started_at_hkt": cfg.get("started_at_hkt"),
        "account_pnl": v1_account_pnl,
        "v1_account_pnl": v1_account_pnl,
        "alpha_account_pnl": alpha_account_pnl,
        "combined_account_pnl": combined_account_pnl,
        "wallet_cash": v1_wallet.get("cash_usdc"),
        "starting_wallet_usdc": v1_start,
        "account_pnl_pct": round(v1_account_pnl / v1_start * 100, 2) if v1_start else None,
        "v1": {
            "label": "V1",
            "baseline_usdc": round(v1_baseline, 2),
            "account_start_usdc": round(v1_start, 2),
            "equity_usdc": round(v1_equity, 2),
            "cash_usdc": v1_wallet.get("cash_usdc"),
            "account_pnl": v1_account_pnl,
        },
        "alpha_max": {
            "label": "Alpha Max",
            "baseline_usdc": round(alpha_baseline, 2),
            "account_start_usdc": round(alpha_start, 2),
            "equity_usdc": round(alpha_equity, 2),
            "cash_usdc": alpha_wallet.get("cash_usdc"),
            "account_pnl": alpha_account_pnl,
        },
        "btc": btc_v1,
        "eth": eth_v1,
        "alpha_btc": alpha_btc,
        "alpha_eth": alpha_eth,
        "bots": bots,
        "totals": {
            "trades_closed": total_closed,
            "log_pnl_gross": total_log,
            "est_fees": total_fees,
            "log_pnl_after_fees": round(total_log - total_fees, 2),
            "fee_pct_of_gross_log": round(total_fees / total_log * 100, 1) if total_log else None,
            "fee_per_trade": round(total_fees / total_closed, 2) if total_closed else None,
            "slippage_in_log": total_slippage,
            "log_overstatement_vs_account": log_overstatement,
            "residual_after_fees": round(log_overstatement - total_fees, 2),
        },
        "note": "Reset to Strategy Monitor start; V1 + Alpha Max (4 bots). Historical session / manual / proof trades excluded.",
    }


def _alpha_stakes(wallet_cash: float) -> dict[str, float]:
    mult = max(1.0, round(float(wallet_cash or 0) / 1000.0, 4))
    return {
        "btc_outside_favorite": 54.0,
        "eth_outside_favorite": 48.0,
        "btc_core_favorite": round(54.0 * 1.5 * mult, 2),
        "eth_core_favorite": round(48.0 * 1.5 * mult, 2),
        "core_underdog": round(90.0 * 1.5 * mult, 2),
    }


def build_alpha_live_dashboard() -> dict[str, Any]:
    alpha_cache = LOG_DIR / "bot2" / "wallet_cache.json"
    _refresh_wallet_cache(key="alpha_max", env_file=ROOT / ".env.bot2", cache_file=alpha_cache)
    wallet = _wallet(alpha_cache)
    rows = _load_json(LOG_DIR / "bot2" / "alpha_max_trades_live.json", [])
    rows = rows if isinstance(rows, list) else []
    sess = _load_json(LOG_DIR / "bot2" / "alpha_max_live_session.json", {})
    state = _load_json(LOG_DIR / "bot2" / "alpha_max_live_state.json", {})
    closed = [r for r in rows if r.get("trade_result") in ("WIN", "LOSS")]
    wins = [r for r in closed if r.get("trade_result") == "WIN"]
    today = _today_hkt()
    today_rows = [r for r in closed if str(r.get("timestamp_hkt") or "").startswith(today)]
    session_pnl = _pnl_with_fees(closed)
    today_pnl = _pnl_with_fees(today_rows)
    pnl = session_pnl["net_pnl"]
    today_net = today_pnl["net_pnl"]
    try:
        from bot_control import bot_control_status

        control = bot_control_status().get("alpha_max", {})
    except Exception:
        control = {"paused": False, "running": True}
    try:
        from alpha_max_live import daily_loss_limit_usd

        loss_limit = daily_loss_limit_usd()
    except Exception:
        loss_limit = 75.0
    daily_release = control.get("daily_stop") or {}
    daily_released = bool(daily_release.get("released"))
    control["daily_stop"] = {
        **daily_release,
        "pnl": round(float(today_net), 2),
        "limit": round(float(loss_limit), 2),
        "hit": float(today_net) <= -abs(float(loss_limit)),
        "effective": float(today_net) <= -abs(float(loss_limit)) and not daily_released,
        "released": daily_released,
    }
    cfg = _load_config()
    start = _resolve_wallet_at_reset(
        cfg,
        "alpha_max",
        wallet_equity=float(wallet.get("total_equity") or 0),
        sess=sess if isinstance(sess, dict) else None,
    )
    cash = float(wallet.get("cash_usdc") or 0)
    equity = float(wallet.get("total_equity") or cash)
    wallet_trusted = _wallet_trusted_for_pnl(wallet)
    if wallet_trusted and start > 0:
        account_pnl = round(equity - start, 2)
        reconcile_delta = round(account_pnl - pnl, 2)
    else:
        account_pnl = None
        reconcile_delta = None
    baseline = float(
        sess.get("competition_baseline_usdc")
        or (cfg.get("baseline_usdc") or {}).get("alpha_max")
        or 1000
    )
    return {
        "ok": True,
        "mode": "live",
        "live_session": sess,
        "live_state": state if isinstance(state, dict) else {},
        "wallet": wallet,
        "control": control,
        "starting_wallet_usdc": round(start, 2),
        "wallet_cash": round(cash, 2),
        "account_pnl": account_pnl,
        "account_pnl_available": wallet_trusted and start > 0,
        "realized_pnl": pnl,
        "gross_pnl": session_pnl["gross_pnl"],
        "estimated_fees_usd": session_pnl["estimated_fees_usd"],
        "reconcile_delta": reconcile_delta,
        "wallet_at_reset_usdc": round(start, 2),
        "competition_baseline_usdc": round(baseline, 2),
        "live_trades_closed": len(closed),
        "live_wins": len(wins),
        "live_losses": len(closed) - len(wins),
        "live_win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "live_gross_pnl": session_pnl["gross_pnl"],
        "live_net_pnl": pnl,
        "live_total_pnl": pnl,
        "live_daily_pnl_hkt": today_net,
        "live_daily_gross_pnl": today_pnl["gross_pnl"],
        "live_daily_fees_usd": today_pnl["estimated_fees_usd"],
        "open_positions": int(wallet.get("positions_open") or 0),
        "stake_reference": _alpha_stakes(cash),
        "daily_loss_limit_usd": loss_limit,
        "recent_trades": [
            _normalize_trade(
                _enrich_alpha_skip_row(r),
                account="Alpha Max",
                bot=f"Alpha {str(r.get('asset') or '').upper()}",
            )
            for r in rows[::-1][:80]
        ],
    }


def build_competition_dashboard(*, refresh_wallets: bool = False) -> dict[str, Any]:
    cfg = _load_config()
    start = _parse_time(cfg.get("started_at_hkt")) if cfg.get("exclude_before_start", True) else None
    baseline = cfg.get("baseline_usdc") or {}

    v1_btc = _rows_since(LOG_DIR / "trade_log_live.json", start)
    v1_eth = _rows_since(LOG_DIR / "trade_log_eth_live.json", start)
    alpha_rows = _rows_since(LOG_DIR / "bot2" / "alpha_max_trades_live.json", start)
    alpha_btc = [r for r in alpha_rows if str(r.get("asset") or "").upper() == "BTC"]
    alpha_eth = [r for r in alpha_rows if str(r.get("asset") or "").upper() == "ETH"]

    bot_cards = [
        _summarize_bot(v1_btc, account="V1", bot="BTC V1"),
        _summarize_bot(v1_eth, account="V1", bot="ETH V1"),
        _summarize_bot(alpha_btc, account="Alpha Max", bot="Alpha BTC", fee_adjusted=True),
        _summarize_bot(alpha_eth, account="Alpha Max", bot="Alpha ETH", fee_adjusted=True),
    ]

    v1_trades = v1_btc + v1_eth
    alpha_trades = alpha_rows
    v1_cache = LOG_DIR / "wallet_cache.json"
    alpha_cache = LOG_DIR / "bot2" / "wallet_cache.json"
    if refresh_wallets:
        refresh_competition_wallets()
    v1_wallet = _wallet(v1_cache)
    alpha_wallet = _wallet(alpha_cache)

    v1_equity = float(v1_wallet.get("total_equity") or v1_wallet.get("cash_usdc") or 0)
    alpha_equity = float(alpha_wallet.get("total_equity") or alpha_wallet.get("cash_usdc") or 0)
    v1_start = _resolve_wallet_at_reset(cfg, "v1", wallet_equity=v1_equity)
    alpha_start = _resolve_wallet_at_reset(cfg, "alpha_max", wallet_equity=alpha_equity)

    accounts = {
        "v1": _account_summary(
            key="v1",
            label="V1",
            baseline=float(baseline.get("v1") or 0),
            account_start=v1_start,
            wallet=v1_wallet,
            bot_summaries=bot_cards[:2],
            trades=v1_trades,
        ),
        "alpha_max": _account_summary(
            key="alpha_max",
            label="Alpha Max",
            baseline=float(baseline.get("alpha_max") or 0),
            account_start=alpha_start,
            wallet=alpha_wallet,
            bot_summaries=bot_cards[2:],
            trades=alpha_trades,
            min_next_stake=48.0,
            fee_adjusted_log=True,
        ),
        "vault": {
            "key": "vault",
            "label": "Vault",
            "baseline_usdc": round(float(baseline.get("vault") or 0), 2),
            "strategy": "Savings only",
        },
        "unallocated": {
            "key": "unallocated",
            "label": "Unallocated",
            "baseline_usdc": round(float(baseline.get("unallocated") or 0), 2),
            "strategy": "Hold / decide later",
        },
    }

    recent = _monitor_recent_trades(v1_btc, v1_eth, alpha_rows)

    total_tracked = sum(float(v) for v in baseline.values() if isinstance(v, (int, float)))
    return {
        "ok": True,
        "enabled": bool(cfg.get("enabled")),
        "competition_started_at_hkt": cfg.get("started_at_hkt"),
        "competition_started_at_utc": start.astimezone(timezone.utc).isoformat() if start else None,
        "baseline_total_usdc": round(total_tracked, 2),
        "accounts": accounts,
        "bot_cards": bot_cards,
        "recent_trades": recent,
        "notes": cfg.get("notes") or [],
    }
