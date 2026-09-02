"""Shared paths and logging for BTC 5m bot (isolated from weather-bot)."""

from __future__ import annotations

import functools
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

ROOT = Path("/root/btc-5m-bot")
LOG_DIR = ROOT / "logs"
ERROR_LOG = LOG_DIR / "error.log"
OBSERVATIONS_FILE = LOG_DIR / "observations.jsonl"
WINDOWS_FILE = LOG_DIR / "windows.json"
WINDOW_STATS_FILE = LOG_DIR / "window_stats.json"
STATE_FILE = LOG_DIR / "observer_state.json"
TRADES_FILE = LOG_DIR / "paper_trades.json"
PAPER_STATE_FILE = LOG_DIR / "paper_state.json"
TRADE_LOG_FILE = LOG_DIR / "trade_log_full.json"  # legacy; migrated to split logs
PAPER_TRADE_LOG_FILE = LOG_DIR / "trade_log_paper.json"
LIVE_TRADE_LOG_FILE = LOG_DIR / "trade_log_live.json"
NEW_STATE_FILE = LOG_DIR / "new_strategy_state.json"
STRATEGY_CONFIG_FILE = LOG_DIR / "strategy_config.json"
LIVE_SESSION_FILE = LOG_DIR / "live_session.json"
ETH_TRADE_LOG_FILE = LOG_DIR / "trade_log_eth_paper.json"
ETH_LIVE_TRADE_LOG_FILE = LOG_DIR / "trade_log_eth_live.json"
ETH_LIVE_SESSION_FILE = LOG_DIR / "eth_live_session.json"
ETH_WINDOWS_FILE = LOG_DIR / "eth_windows.json"
ETH_WINDOW_STATS_FILE = LOG_DIR / "eth_window_stats.json"
ETH_STATE_FILE = LOG_DIR / "eth_observer_state.json"
ETH_OBSERVATIONS_FILE = LOG_DIR / "eth_observations.jsonl"
T = TypeVar("T")
_logger: logging.Logger | None = None


def setup_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _logger = logging.getLogger("btc-5m-bot")
    _logger.setLevel(logging.INFO)
    if not _logger.handlers:
        fh = logging.FileHandler(ERROR_LOG, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _logger.addHandler(fh)
    return _logger


def log_error(msg: str, exc: Exception | None = None) -> None:
    logger = setup_logger()
    if exc:
        logger.error("%s: %s", msg, exc, exc_info=True)
    else:
        logger.error(msg)


def retry_api(
    attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            wait = delay
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    log_error(f"{fn.__name__} attempt {attempt}/{attempts} failed", exc)
                    if attempt < attempts:
                        time.sleep(wait)
                        wait *= backoff
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


def _compute_window_stats(windows: list[dict]) -> dict:
    from collections import Counter

    tradable = [w for w in windows if w.get("had_tradable_entry")]
    skip_counter: Counter = Counter()
    for w in windows:
        for cp in w.get("checkpoints", []):
            for r in cp.get("skip_reasons") or []:
                skip_counter[r.split("_")[0] + "_*"] += 1
    total = len(windows)
    stats = {
        "total_windows": total,
        "tradable_windows": len(tradable),
        "tradable_pct": round(len(tradable) / total * 100, 1) if total else 0,
        "hypo_wins": sum(1 for w in tradable if w.get("hypo_win")),
        "skip_reasons": dict(skip_counter.most_common(10)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tradable_n = stats["tradable_windows"]
    if tradable_n:
        stats["hypo_win_rate"] = round(stats["hypo_wins"] / tradable_n * 100, 1)
    else:
        stats["hypo_win_rate"] = None
    return stats


def write_window_stats(windows: list[dict]) -> None:
    """Lightweight cache for dashboard (avoids loading 4MB windows.json on each page view)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    WINDOW_STATS_FILE.write_text(json.dumps(_compute_window_stats(windows), indent=2))


def write_eth_window_stats(windows: list[dict]) -> None:
    """ETH-only window stats (do not overwrite BTC cache)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ETH_WINDOW_STATS_FILE.write_text(json.dumps(_compute_window_stats(windows), indent=2))


def read_window_stats() -> dict:
    if not WINDOW_STATS_FILE.exists():
        return {
            "total_windows": 0,
            "tradable_windows": 0,
            "tradable_pct": 0,
            "hypo_wins": 0,
            "hypo_win_rate": None,
            "skip_reasons": {},
        }
    return json.loads(WINDOW_STATS_FILE.read_text())


def _hkt_short_date(hkt: str | None) -> str:
    """'2026-06-18 12:00:00 HKT' → '06-18'."""
    if not hkt or len(hkt) < 10:
        return ""
    return hkt[:10][5:]


def build_equity_curve_with_labels(
    start: float,
    closed: list[dict],
    *,
    session_start_hkt: str | None = None,
    pad_to: float | None = None,
) -> tuple[list[float], list[str]]:
    """Build equity curve + x-axis labels: MM-DD (Start) and MM-DD (Tn) per settled trade."""
    start_date = _hkt_short_date(session_start_hkt)
    start_label = f"{start_date} (Start)" if start_date else "Start"
    curve: list[float] = [start]
    labels: list[str] = [start_label]
    running = start
    for i, row in enumerate(sorted(closed, key=lambda x: x.get("timestamp_hkt", "")), start=1):
        running = round(running + float(row.get("pnl") or 0), 2)
        curve.append(running)
        d = _hkt_short_date(row.get("timestamp_hkt") or row.get("resolved_at_hkt"))
        labels.append(f"{d} (T{i})" if d else f"T{i}")
    if pad_to is not None and len(curve) == 1:
        curve.append(round(pad_to, 2))
        labels.append(f"{_hkt_short_date(session_start_hkt) or '—'} (Now)")
    return curve, labels


def build_daily_pnl_series(rows: list[dict]) -> list[dict[str, Any]]:
    """Group closed bot trade PnL by HKT calendar date."""
    by_date: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in rows:
        if not row.get("entered"):
            continue
        if row.get("trade_result") not in ("WIN", "LOSS"):
            continue
        day = (row.get("timestamp_hkt") or row.get("resolved_at_hkt") or "")[:10]
        if len(day) < 10:
            continue
        pnl = float(row.get("pnl") or 0)
        by_date[day] = round(by_date.get(day, 0.0) + pnl, 2)
        counts[day] = counts.get(day, 0) + 1
    out: list[dict[str, Any]] = []
    for day in sorted(by_date):
        pnl = by_date[day]
        out.append(
            {
                "date": day,
                "label": day[5:],
                "pnl": pnl,
                "trades": counts[day],
            }
        )
    return out


def merge_daily_pnl_series(*series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sum daily PnL dicts (e.g. BTC + ETH → total)."""
    by_date: dict[str, dict[str, Any]] = {}
    for items in series:
        for item in items:
            day = item["date"]
            block = by_date.setdefault(
                day,
                {"date": day, "label": item["label"], "pnl": 0.0, "trades": 0},
            )
            block["pnl"] = round(float(block["pnl"]) + float(item["pnl"]), 2)
            block["trades"] = int(block["trades"]) + int(item.get("trades") or 0)
    return [by_date[d] for d in sorted(by_date)]


def _closed_log_pnl(rows: list[dict]) -> float:
    closed = [
        r for r in rows
        if r.get("entered") and r.get("trade_result") in ("WIN", "LOSS")
    ]
    return round(sum(float(r.get("pnl") or 0) for r in closed), 2)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def account_pnl_snapshot(*, live_wallet: bool = True) -> dict[str, Any]:
    """Reconcile shared Polymarket wallet vs per-bot trade logs."""
    from new_strategy import fetch_wallet_cash

    wallet = round(float(fetch_wallet_cash(live=live_wallet)), 2)
    sess = _read_json(LIVE_SESSION_FILE, {})
    start = float(sess.get("wallet_at_reset_usdc") or sess.get("starting_wallet_usdc") or 0)
    session_id = sess.get("session_id")
    started_at_hkt = sess.get("started_at_hkt")
    if start <= 0:
        eth_sess = _read_json(ETH_LIVE_SESSION_FILE, {})
        start = float(
            eth_sess.get("wallet_at_reset_usdc")
            or eth_sess.get("starting_wallet_usdc")
            or eth_sess.get("starting_bankroll")
            or 0
        )
        if not session_id:
            session_id = eth_sess.get("session_id")
        if not started_at_hkt:
            started_at_hkt = eth_sess.get("started_at_hkt")

    account_pnl = round(wallet - start, 2) if start > 0 else None
    account_pnl_pct = (
        round(account_pnl / start * 100, 2)
        if account_pnl is not None and start > 0
        else None
    )

    btc_rows = _read_json(LIVE_TRADE_LOG_FILE, [])
    if session_id:
        btc_rows = [r for r in btc_rows if r.get("session_id") == session_id]
    btc_log_pnl = _closed_log_pnl(btc_rows)

    eth_rows = _read_json(ETH_LIVE_TRADE_LOG_FILE, [])
    eth_sess_id = _read_json(ETH_LIVE_SESSION_FILE, {}).get("session_id")
    if eth_sess_id:
        eth_rows = [r for r in eth_rows if r.get("session_id") == eth_sess_id]
    eth_log_pnl = _closed_log_pnl(eth_rows)

    manual_session_pnl = 0.0
    manual_daily_pnl = 0.0
    manual_closed = 0
    try:
        from manual_bets import manual_bets_summary

        ms = manual_bets_summary(auto_sync=False)
        manual_session_pnl = float(ms.get("session_pnl") or 0)
        manual_daily_pnl = float(ms.get("daily_pnl_hkt") or 0)
        manual_closed = int(ms.get("closed") or 0)
    except Exception:
        pass

    bots_log_sum = round(btc_log_pnl + eth_log_pnl, 2)
    bot_daily_pnl = 0.0
    try:
        from package2_strategy import v1_combined_daily_pnl

        bot_daily_pnl = float(v1_combined_daily_pnl())
    except Exception:
        pass

    attributed_pnl = round(bots_log_sum + manual_session_pnl, 2)
    reconcile_delta = (
        round(account_pnl - attributed_pnl, 2) if account_pnl is not None else None
    )

    bot_target_wallet = round(start + bots_log_sum, 2) if start > 0 else None
    wallet_gap_bot = (
        round(wallet - bot_target_wallet, 2)
        if bot_target_wallet is not None
        else None
    )
    # Positive = withdraw this much now to align wallet with bot-only performance.
    withdraw_to_bot_target = (
        round(max(0.0, wallet - bot_target_wallet), 2)
        if bot_target_wallet is not None
        else None
    )
    deposit_to_bot_target = (
        round(max(0.0, bot_target_wallet - wallet), 2)
        if bot_target_wallet is not None
        else None
    )

    flows_summary: dict[str, Any] = {}
    try:
        from wallet_flows import wallet_flows_summary

        flows_summary = wallet_flows_summary(
            account_pnl=account_pnl,
            bots_log_sum=bots_log_sum,
            manual_session_pnl=manual_session_pnl,
            starting_wallet_usdc=start,
            wallet_cash=wallet,
        )
    except Exception:
        pass

    withdrawals_total = float(flows_summary.get("withdrawals_total_usd") or 0)
    deposits_total = float(flows_summary.get("deposits_total_usd") or 0)
    gross_trading_pnl = float(flows_summary.get("gross_trading_pnl_usd") or attributed_pnl)
    reconcile_after_flows = flows_summary.get("reconcile_after_flows_usd")

    return {
        "wallet_cash": wallet,
        "starting_wallet_usdc": round(start, 2),
        "session_id": session_id,
        "started_at_hkt": started_at_hkt,
        "account_pnl": account_pnl,
        "account_pnl_pct": account_pnl_pct,
        "btc_log_pnl": btc_log_pnl,
        "eth_log_pnl": eth_log_pnl,
        "bots_log_sum": bots_log_sum,
        "bot_pnl_session": bots_log_sum,
        "bot_daily_pnl_hkt": round(bot_daily_pnl, 2),
        "manual_pnl": round(manual_session_pnl, 2),
        "manual_pnl_session": round(manual_session_pnl, 2),
        "manual_daily_pnl_hkt": round(manual_daily_pnl, 2),
        "manual_closed": manual_closed,
        "gross_trading_pnl": round(gross_trading_pnl, 2),
        "withdrawals_total_usd": round(withdrawals_total, 2),
        "withdrawals_explicit_usd": flows_summary.get("withdrawals_explicit_usd", 0),
        "withdrawals_inferred_usd": flows_summary.get("withdrawals_inferred_usd", 0),
        "deposits_total_usd": round(deposits_total, 2),
        "net_flows_usd": flows_summary.get("net_flows_usd", 0),
        "expected_wallet_usd": flows_summary.get("expected_wallet_usd"),
        "bot_target_wallet_usd": bot_target_wallet,
        "wallet_gap_vs_bot_usd": wallet_gap_bot,
        "withdraw_to_bot_target_usd": withdraw_to_bot_target,
        "deposit_to_bot_target_usd": deposit_to_bot_target,
        "attributed_pnl": attributed_pnl,
        "reconcile_delta": reconcile_delta,
        "reconcile_after_flows_usd": reconcile_after_flows,
        "wallet_flows": flows_summary,
        "shared_wallet": True,
        "formula": (
            "Bot 目標錢包 = 起始 + Bot PnL · "
            "現金 > 目標 → 提走差額 · 現金 < 目標 → 補回差額"
        ),
        "bot_only_note": "BTC/ETH bot cards show trade-log PnL only — manual bets excluded.",
    }


def _est_entry_fee(row: dict) -> float:
    from polymarket_fees import estimate_taker_fee

    stake = float(row.get("stake") or 0)
    ask = float(row.get("entry_ask") or row.get("fill_price") or 0)
    if ask <= 0 or stake <= 0:
        return 0.0
    return estimate_taker_fee(stake / ask, ask)


def _slippage_pnl_impact(row: dict) -> float:
    from new_strategy import calc_win_pnl

    decision = row.get("decision_ask")
    fill = row.get("entry_ask") or row.get("fill_price")
    stake = float(row.get("stake") or 0)
    if decision is None or fill is None or stake <= 0:
        return 0.0
    if row.get("trade_result") != "WIN":
        return 0.0
    return round(calc_win_pnl(stake, float(fill)) - calc_win_pnl(stake, float(decision)), 2)


def _analyze_bot_costs(rows: list[dict], name: str) -> dict[str, Any]:
    closed = [
        r for r in rows
        if r.get("entered") and r.get("trade_result") in ("WIN", "LOSS")
    ]
    log_pnl = _closed_log_pnl(closed)
    fees = round(sum(_est_entry_fee(r) for r in closed), 2)
    slips = [_slippage_pnl_impact(r) for r in closed]
    slip_total = round(sum(slips), 2)
    slip_worse = round(sum(s for s in slips if s < 0), 2)
    slip_better = round(sum(s for s in slips if s > 0), 2)
    stakes = round(sum(float(r.get("stake") or 0) for r in closed), 2)
    wins = sum(1 for r in closed if r.get("trade_result") == "WIN")
    return {
        "bot": name,
        "trades_closed": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "stakes_usd": stakes,
        "log_pnl": log_pnl,
        "est_fees": fees,
        "log_pnl_net": round(log_pnl - fees, 2),
        "fee_pct_of_log": round(fees / log_pnl * 100, 1) if log_pnl else None,
        "slippage_in_log": slip_total,
        "slippage_worse": slip_worse,
        "slippage_better": slip_better,
        "note_slippage": "已在 trade log 內，非額外帳戶差額",
    }


def session_cost_breakdown(*, live_wallet: bool = True) -> dict[str, Any]:
    """Session cost drag: fees, slippage (in log), account vs gross log."""
    acct = account_pnl_snapshot(live_wallet=live_wallet)
    btc_sess = acct.get("session_id")
    eth_sess = _read_json(ETH_LIVE_SESSION_FILE, {}).get("session_id")

    btc_rows = _read_json(LIVE_TRADE_LOG_FILE, [])
    if btc_sess:
        btc_rows = [r for r in btc_rows if r.get("session_id") == btc_sess]
    eth_rows = _read_json(ETH_LIVE_TRADE_LOG_FILE, [])
    if eth_sess:
        eth_rows = [r for r in eth_rows if r.get("session_id") == eth_sess]

    btc = _analyze_bot_costs(btc_rows, "BTC")
    eth = _analyze_bot_costs(eth_rows, "ETH")
    total_log = round(btc["log_pnl"] + eth["log_pnl"], 2)
    total_fees = round(btc["est_fees"] + eth["est_fees"], 2)
    total_net = round(total_log - total_fees, 2)
    total_slip = round(btc["slippage_in_log"] + eth["slippage_in_log"], 2)

    account_pnl = acct.get("account_pnl")
    log_overstatement = (
        round(total_log - account_pnl, 2) if account_pnl is not None else None
    )
    residual = (
        round(log_overstatement - total_fees, 2)
        if log_overstatement is not None
        else None
    )

    return {
        **acct,
        "btc": btc,
        "eth": eth,
        "totals": {
            "trades_closed": btc["trades_closed"] + eth["trades_closed"],
            "log_pnl_gross": total_log,
            "est_fees": total_fees,
            "log_pnl_after_fees": total_net,
            "fee_pct_of_gross_log": round(total_fees / total_log * 100, 1) if total_log else None,
            "fee_per_trade": round(total_fees / (btc["trades_closed"] + eth["trades_closed"]), 2)
            if btc["trades_closed"] + eth["trades_closed"]
            else None,
            "slippage_in_log": total_slip,
            "log_overstatement_vs_account": log_overstatement,
            "residual_after_fees": residual,
        },
        "manual_pnl": acct.get("manual_pnl_session", acct.get("manual_pnl", 0)),
        "manual_pnl_session": acct.get("manual_pnl_session", acct.get("manual_pnl", 0)),
    }
