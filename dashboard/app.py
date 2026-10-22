#!/usr/bin/env python3
"""Flask dashboard — Package 2+ live BTC + ETH (paper or live)."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import Counter
from typing import Any
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request

DASHBOARD_DIR = Path(__file__).resolve().parent
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))
ROOT = Path(os.getenv("BTC_5M_ROOT", "/root/btc-5m-bot"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from bot_common import (  # noqa: E402
    ETH_LIVE_TRADE_LOG_FILE,
    ETH_OBSERVATIONS_FILE,
    LIVE_TRADE_LOG_FILE,
    OBSERVATIONS_FILE,
    STATE_FILE,
    ETH_STATE_FILE,
    account_pnl_snapshot,
    session_cost_breakdown,
    STRATEGY_CONFIG_FILE,
    build_daily_pnl_series,
    merge_daily_pnl_series,
    read_window_stats,
)
from eth_paper_engine import (  # noqa: E402
    _session_rows,
    _filter_session_rows,
    backfill_eth_window_decisions,
    eth_daily_loss_limit_hit,
    eth_summary,
    eth_trading_mode,
    load_active_eth_log,
)
from paper_engine import (  # noqa: E402
    _filter_strategy_rows,
    backfill_live_log_from_paper,
    backfill_window_trade_decisions,
    dedupe_session_trade_logs,
    load_active_strategy_log,
    load_live_session,
    new_strategy_summary,
    repair_live_log_stakes,
)
from package2_strategy import (  # noqa: E402
    clear_tier_pause,
    daily_loss_limit_hit as btc_daily_loss_limit_hit,
    daily_realized_pnl_hkt as package2_daily_realized_pnl_hkt,
    get_extended_hours_live_status,
    package2_config,
    risk_pause_status,
    set_btc_extended_mode,
    set_extended_hours_live,
    tiers_preview,
    v1_combined_daily_loss_limit_hit,
    v1_combined_daily_loss_limit_usd,
    v1_combined_daily_pnl,
)
from polymarket_crypto import (  # noqa: E402
    discover_current_market,
    fetch_event_by_slug,
    fetch_market_outcome,
    fetch_order_book,
    seconds_to_end,
    slug_for,
    summarize_book,
)
from polymarket_wallet import fetch_wallet_status  # noqa: E402
from price_feeds import fetch_btc_usd, fetch_eth_usd  # noqa: E402
from strategy_rules import CUTOFF_T, ENTRY_T_MAX, ENTRY_T_MIN, MIN_BTC_MOVE_USD, MIN_DEPTH_USD, MAX_SPREAD  # noqa: E402
from weather_service import safe_weather_summary, safe_weather_trades  # noqa: E402
from ops_metrics import build_daily_ops_report, execution_metrics  # noqa: E402
from manual_bets import manual_bets_summary  # noqa: E402
from manual_bets_sync import maybe_sync_manual_bets, sync_manual_bets_from_activity  # noqa: E402
from strategy_overview import build_strategy_overview  # noqa: E402
from strategy_display import build_strategy_display  # noqa: E402
from bot_control import bot_control_status, release_daily_stop, set_bot_paused, load_bot_control, _daily_stop_release_status  # noqa: E402
from backtest_research import refresh_live_session_research  # noqa: E402
app = Flask(__name__, template_folder="templates", static_folder="static")
# Single-user dashboard: reload index.html on each request so layout edits show without restart.
app.config["TEMPLATES_AUTO_RELOAD"] = True
HKT = ZoneInfo("Asia/Hong_Kong")

_BG_STARTED = False
_BG_LOCK = threading.Lock()
_BG_INTERVAL_SEC = 90.0


def _dashboard_background_maintenance() -> None:
    try:
        if os.getenv("TRADING_MODE", "paper").lower() == "live":
            dedupe_session_trade_logs()
            backfill_window_trade_decisions()
        backfill_eth_window_decisions()
    except Exception:
        pass


def _dashboard_background_loop() -> None:
    while True:
        _dashboard_background_maintenance()
        time.sleep(_BG_INTERVAL_SEC)


def _ensure_dashboard_background() -> None:
    global _BG_STARTED
    with _BG_LOCK:
        if _BG_STARTED:
            return
        _BG_STARTED = True
        threading.Thread(target=_dashboard_background_loop, daemon=True, name="dash-bg").start()


@app.before_request
def _kick_dashboard_background():
    _ensure_dashboard_background()


@app.after_request
def _no_cache_html(response):
    ct = response.content_type or ""
    if "text/html" in ct:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def _load_config() -> dict:
    if STRATEGY_CONFIG_FILE.exists():
        return json.loads(STRATEGY_CONFIG_FILE.read_text())
    return {}


def _load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


_markets_live_cache: dict[str, Any] = {"ts": 0.0, "data": None}
MARKETS_LIVE_TTL_SEC = 8.0
WINDOW_SEC_5M = 300


def _tail_observations_for_slug(slug: str, obs_file: Path, price_key: str, open_key: str) -> list[dict[str, Any]]:
    """Read recent observation rows for one 5m window (tail scan, low RAM)."""
    if not obs_file.exists():
        return []
    try:
        size = obs_file.stat().st_size
        with obs_file.open("rb") as f:
            f.seek(max(0, size - 600_000))
            chunk = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return []

    rows: list[dict[str, Any]] = []
    for line in chunk.splitlines():
        if slug not in line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("slug") != slug:
            continue
        t_rem = float(row.get("t_remaining") or 0)
        price = row.get(price_key)
        if price is None:
            continue
        up_book = row.get("up_book") or {}
        down_book = row.get("down_book") or {}
        rows.append(
            {
                "elapsed": round(WINDOW_SEC_5M - t_rem, 1),
                "t_remaining": round(t_rem, 1),
                "price": float(price),
                "open_price": float(row.get(open_key) or 0) or None,
                "up_ask": up_book.get("best_ask"),
                "down_ask": down_book.get("best_ask"),
            }
        )

    # Dedupe near-identical elapsed samples; keep latest per ~3s bucket
    by_bucket: dict[int, dict[str, Any]] = {}
    for r in rows:
        bucket = int(float(r["elapsed"]) // 3)
        by_bucket[bucket] = r
    return sorted(by_bucket.values(), key=lambda x: float(x["elapsed"]))


def _open_price_from_observer_state(asset: str, slug: str) -> float | None:
    path = ETH_STATE_FILE if asset == "eth" else STATE_FILE
    open_key = "open_eth_price" if asset == "eth" else "open_btc_price"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("current_slug") == slug:
        val = data.get(open_key)
        if val:
            return float(val)
    wr = data.get("window_record") or {}
    if wr.get("slug") == slug:
        val = wr.get(open_key)
        if val:
            return float(val)
    return None


def _build_window_curve(
    *,
    asset: str,
    slug: str,
    spot: dict[str, Any],
    t_remaining: float,
    up_book: dict[str, Any],
    down_book: dict[str, Any],
) -> dict[str, Any]:
    asset = asset.lower()
    if asset == "eth":
        obs_file = ETH_OBSERVATIONS_FILE
        price_key, open_key = "eth_price", "open_eth_price"
    else:
        obs_file = OBSERVATIONS_FILE
        price_key, open_key = "btc_price", "open_btc_price"

    points = _tail_observations_for_slug(slug, obs_file, price_key, open_key)
    open_price = next((p["open_price"] for p in points if p.get("open_price")), None)
    if open_price is None:
        open_price = _open_price_from_observer_state(asset, slug)

    current = float(spot.get("price") or 0)
    elapsed_now = max(0.0, round(WINDOW_SEC_5M - float(t_remaining), 1))
    if current > 0:
        live_pt = {
            "elapsed": elapsed_now,
            "t_remaining": round(float(t_remaining), 1),
            "price": current,
            "open_price": open_price,
            "up_ask": up_book.get("best_ask"),
            "down_ask": down_book.get("best_ask"),
            "live": True,
        }
        if not points or abs(float(points[-1]["elapsed"]) - elapsed_now) > 1.5:
            points.append(live_pt)
        else:
            points[-1] = live_pt

    if open_price is not None:
        for p in points:
            p["open_price"] = open_price

    delta_usd = round(current - open_price, 2) if open_price else 0.0
    delta_pct = round(delta_usd / open_price * 100, 3) if open_price else 0.0
    leading = "up" if delta_usd >= 0 else "down"
    up_ask = up_book.get("best_ask")
    down_ask = down_book.get("best_ask")

    return {
        "window_sec": WINDOW_SEC_5M,
        "open_price": open_price,
        "current_price": current,
        "price_source": spot.get("source"),
        "delta_usd": delta_usd,
        "delta_pct": delta_pct,
        "leading": leading,
        "up_ask": up_ask,
        "down_ask": down_ask,
        "up_pct": round(float(up_ask) * 100, 1) if up_ask is not None else None,
        "down_pct": round(float(down_ask) * 100, 1) if down_ask is not None else None,
        "points": points,
    }


def _last_round_polymarket(asset: str, current_window_start: int) -> dict[str, Any] | None:
    """Previous 5m window — official Polymarket resolution (Gamma API)."""
    prev_slug = slug_for(asset.lower(), current_window_start - WINDOW_SEC_5M)
    event = fetch_event_by_slug(prev_slug)
    if not event:
        return None
    pm = fetch_market_outcome(prev_slug)
    outcome = (pm or {}).get("outcome")
    resolved = outcome in ("up", "down")
    return {
        "slug": prev_slug,
        "title": event.get("title") or prev_slug,
        "outcome": outcome.upper() if resolved else None,
        "resolved": resolved,
        "source": "polymarket",
        "outcome_prices": (pm or {}).get("outcome_prices"),
        "outcomes": (pm or {}).get("outcomes"),
        "resolution_status": (pm or {}).get("resolution_status"),
    }


def _live_status(asset: str = "btc") -> dict:
    try:
        market = discover_current_market(asset)
        px_fn = fetch_btc_usd if asset == "btc" else fetch_eth_usd
        spot = px_fn()
        if not market:
            return {"status": "no_market", "asset": asset, "spot": spot}
        t_rem = seconds_to_end(market)
        up = summarize_book(fetch_order_book(market.up_token_id))
        down = summarize_book(fetch_order_book(market.down_token_id))
        curve = _build_window_curve(
            asset=asset,
            slug=market.slug,
            spot=spot,
            t_remaining=t_rem,
            up_book=up,
            down_book=down,
        )
        last_round = _last_round_polymarket(asset, market.window_start_ts)
        return {
            "status": "ok",
            "asset": asset,
            "spot": spot,
            "market": {
                "title": market.title,
                "slug": market.slug,
                "t_remaining": round(t_rem, 1),
                "window_start_ts": market.window_start_ts,
                "window_end_ts": market.window_end_ts,
                "poly_up_pct": round(market.up_price * 100, 1),
                "poly_down_pct": round(market.down_price * 100, 1),
            },
            "up_book": up,
            "down_book": down,
            "curve": curve,
            "last_round": last_round,
        }
    except Exception as exc:
        return {"status": "error", "asset": asset, "message": str(exc)}



@app.route("/health")
def health():
    return jsonify({"ok": True, "bot": "btc-5m-package2plus"})


@app.route("/api/services-status")
def api_services_status():
    import subprocess

    units = {
        "btc-5m-observer": "BTC 5m",
        "eth-5m-observer": "ETH 5m",
        "crypto-15m-observer": "15m " + os.getenv("CRYPTO_15M_TRADING_MODE", "paper").upper(),
        "btc-5m-dashboard": "Dashboard",
    }
    out: dict[str, Any] = {}
    for unit, label in units.items():
        try:
            r = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True,
                text=True,
                timeout=2,
            )
            state = (r.stdout or "").strip() or "unknown"
        except Exception:
            state = "unknown"
        out[unit] = {"label": label, "state": state, "active": state == "active"}
    try:
        from paper_board import funding_carry_service_row

        out["funding-carry-loop"] = funding_carry_service_row()
    except Exception:
        out["funding-carry-loop"] = {
            "label": "Funding PAPER",
            "state": "unknown",
            "active": False,
            "via": "error",
        }
    return jsonify(out)


@app.route("/api/all-paper-strategies")
def api_all_paper_strategies():
    """Bots running now, paper ledgers, and research — display only."""
    from paper_board import build_all_paper_strategies

    return jsonify(build_all_paper_strategies(bot_root_path=ROOT))


@app.route("/api/strategy-overview")
def api_strategy_overview():
    return jsonify(build_strategy_overview())


@app.route("/api/timetable")
def api_timetable():
    config = _load_config()
    extended_hours = get_extended_hours_live_status()
    eth = eth_summary()
    eth_rules = eth.get("eth_rules") or {}
    mode = os.getenv("TRADING_MODE", "paper").upper()
    eth_mode = eth_trading_mode().upper()
    sd = build_strategy_display(
        config,
        extended_hours,
        entry_window=f"T-{ENTRY_T_MAX} → T-{ENTRY_T_MIN}",
        eth_rules=eth_rules,
        btc_live=(mode == "LIVE"),
        eth_live=(eth_mode == "LIVE"),
    )
    return jsonify(sd.get("timetable") or {})


@app.route("/api/wallet-flows")
def api_wallet_flows():
    try:
        from wallet_flows import wallet_flows_summary
        from bot_common import account_pnl_snapshot

        acct = account_pnl_snapshot(live_wallet=True)
        summary = wallet_flows_summary(
            account_pnl=acct.get("account_pnl"),
            bots_log_sum=float(acct.get("bots_log_sum") or 0),
            manual_session_pnl=float(acct.get("manual_pnl_session") or 0),
            starting_wallet_usdc=float(acct.get("starting_wallet_usdc") or 0),
            wallet_cash=float(acct.get("wallet_cash") or 0),
        )
        return jsonify(summary)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/manual-bets")
def api_manual_bets():
    # Keep dashboard reads fast, especially on mobile. External activity sync is
    # available via /api/manual-bets/sync and should not block the main UI.
    return jsonify(manual_bets_summary(auto_sync=False))


@app.route("/api/manual-bets/sync", methods=["POST"])
def api_manual_bets_sync():
    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    try:
        return jsonify(maybe_sync_manual_bets(force=force))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _build_overview_daily_pnl() -> dict[str, Any]:
    """Daily closed-trade PnL for overview charts (current session, HKT dates)."""
    if os.getenv("TRADING_MODE", "paper").lower() == "live":
        dedupe_session_trade_logs()
    backfill_eth_window_decisions()
    from eth_paper_engine import load_eth_session

    btc_rows = _filter_strategy_rows(load_active_strategy_log())
    eth_rows = _session_rows(load_active_eth_log())
    btc = build_daily_pnl_series(btc_rows)
    eth = build_daily_pnl_series(eth_rows)
    total = merge_daily_pnl_series(btc, eth)
    return {
        "btc": btc,
        "eth": eth,
        "total": total,
        "session_btc": load_live_session().get("session_id"),
        "session_eth": load_eth_session().get("session_id"),
    }


@app.route("/api/daily-pnl")
def api_daily_pnl():
    return jsonify(_build_overview_daily_pnl())


@app.route("/api/summary")
def api_summary():
    return jsonify(new_strategy_summary(live_wallet=False))


@app.route("/api/trade-log")
def api_trade_log():
    limit = min(int(request.args.get("limit", 150)), 500)
    rows = _filter_strategy_rows(load_active_strategy_log())
    return jsonify(rows[::-1][:limit])


@app.route("/api/config")
def api_config():
    return jsonify(_load_config())


@app.route("/api/tiers")
def api_tiers():
    return jsonify(tiers_preview())


@app.route("/api/wallet")
def api_wallet():
    try:
        btc_paper = os.getenv("TRADING_MODE", "paper").lower() == "paper"
        eth_paper = os.getenv("ETH_TRADING_MODE", "paper").lower() == "paper"
        if btc_paper and eth_paper:
            from package2_strategy import v1_bankroll_snapshot, v1_paper_session

            snap = v1_bankroll_snapshot()
            sess = v1_paper_session()
            return jsonify(
                {
                    "cash_usdc": snap["wallet_cash"],
                    "available_usdc": snap["bankroll"],
                    "api_ready": True,
                    "paper": True,
                    "stale": False,
                    "trading_mode": "paper",
                    "eth_trading_mode": "paper",
                    "paper_session_id": sess.get("session_id"),
                    "paper_started_at_hkt": sess.get("started_at_hkt"),
                }
            )
        w = fetch_wallet_status()
        w["trading_mode"] = os.getenv("TRADING_MODE", "paper").lower()
        w["eth_trading_mode"] = os.getenv("ETH_TRADING_MODE", "paper").lower()
        if w["trading_mode"] == "live" or w["eth_trading_mode"] == "live":
            w["account"] = session_cost_breakdown(live_wallet=True)
        return jsonify(w)
    except Exception as exc:
        return jsonify({"error": str(exc), "api_ready": False, "trading_mode": os.getenv("TRADING_MODE", "paper")}), 500


@app.route("/api/stake-plan")
def api_stake_plan():
    try:
        from package2_strategy import v1_stake_plan_snapshot

        return jsonify(v1_stake_plan_snapshot())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/account-pnl")
def api_account_pnl():
    try:
        return jsonify(account_pnl_snapshot(live_wallet=True))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/competition")
def api_competition():
    try:
        from competition_dashboard import build_competition_dashboard, refresh_competition_wallets

        refresh = request.args.get("refresh_wallets", "0") == "1"
        return jsonify(build_competition_dashboard(refresh_wallets=refresh))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/pnl-cost-breakdown")
def api_pnl_cost_breakdown():
    try:
        return jsonify(session_cost_breakdown(live_wallet=True))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/monitor-fee-breakdown")
def api_monitor_fee_breakdown():
    try:
        from competition_dashboard import build_monitor_fee_breakdown

        return jsonify(build_monitor_fee_breakdown())
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _trade_ts(row: dict) -> str:
    return str(row.get("timestamp_utc") or row.get("timestamp_hkt") or row.get("logged_at") or "")


@app.route("/api/overview-trades")
def api_overview_trades():
    raw_limit = request.args.get("limit", "10")
    if str(raw_limit).lower() in ("all", "0", "none"):
        limit: int | None = None
    else:
        limit = max(1, int(raw_limit))
    btc_rows = [
        {**r, "bot": "BTC"}
        for r in _filter_strategy_rows(load_active_strategy_log())
        if r.get("entered")
    ]
    eth_rows = [
        {**r, "bot": "ETH"}
        for r in _session_rows(load_active_eth_log())
        if r.get("entered")
    ]
    merged = btc_rows + eth_rows
    merged.sort(key=_trade_ts, reverse=True)
    if limit is None:
        return jsonify(merged)
    return jsonify(merged[:limit])


def _v1_detail_rows() -> list[dict[str, Any]]:
    btc_rows = [
        {**r, "bot": "BTC"}
        for r in _filter_strategy_rows(load_active_strategy_log())
    ]
    eth_rows = [
        {**r, "bot": "ETH"}
        for r in _session_rows(load_active_eth_log())
    ]
    merged = btc_rows + eth_rows
    merged.sort(key=_trade_ts, reverse=True)
    return merged


def _v1_row_status(row: dict[str, Any]) -> str:
    if row.get("entered") and row.get("trade_result") in ("WIN", "LOSS"):
        return "closed"
    if row.get("entered") and (
        row.get("trade_result") == "FAILED" or row.get("status") == "failed"
    ):
        return "failed"
    if row.get("entered"):
        return str(row.get("status") or "entered")
    return "skipped"


@app.route("/api/v1-detail-log")
def api_v1_detail_log():
    asset = request.args.get("asset", "all").upper()
    status = request.args.get("status", "all").lower()
    limit = min(max(1, int(request.args.get("limit", 250))), 2000)
    rows = _v1_detail_rows()
    if asset in ("BTC", "ETH"):
        rows = [r for r in rows if r.get("bot") == asset]
    if status == "entered":
        rows = [r for r in rows if r.get("entered")]
    elif status == "unsuccessful":
        rows = [r for r in rows if _v1_row_status(r) in ("skipped", "failed")]
    elif status == "closed":
        rows = [r for r in rows if _v1_row_status(r) == "closed"]
    elif status not in ("all", ""):
        rows = [r for r in rows if _v1_row_status(r) == status]
    return jsonify(rows[:limit])


@app.route("/api/session-research")
def api_session_research():
    """Fav/dog PnL by US core vs non-core — for strategy validation."""
    path = ROOT / "logs" / "session_research_live.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if data.get("btc") or data.get("eth"):
                return jsonify(data)
        except json.JSONDecodeError:
            pass
    return jsonify(refresh_live_session_research())


@app.route("/api/tier-pause", methods=["GET", "POST"])
def api_tier_pause():
    """Tier loss-streak pause status and manual resume."""
    if request.method == "GET":
        return jsonify({
            "btc": risk_pause_status(asset="btc"),
            "eth": risk_pause_status(asset="eth"),
        })
    data = request.get_json(silent=True) or {}
    asset = str(data.get("asset", "")).lower()
    tier = str(data.get("tier", "all")).lower()
    if asset not in ("btc", "eth"):
        return jsonify({"error": "asset must be btc or eth"}), 400
    try:
        clear_tier_pause(asset=asset, tier=tier)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "ok": True,
        "asset": asset,
        "tier": tier,
        "btc": risk_pause_status(asset="btc"),
        "eth": risk_pause_status(asset="eth"),
    })


@app.route("/api/bot-control", methods=["GET", "POST"])
def api_bot_control():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        asset = str(data.get("asset", "")).lower()
        action = str(data.get("action", "")).lower()
        if asset in ("alpha", "alpha-max"):
            asset = "alpha_max"
        if asset not in ("btc", "eth", "alpha_max"):
            return jsonify({"error": "asset must be btc, eth, or alpha_max"}), 400
        if action == "release_daily_stop":
            release_daily_stop(asset)
            payload = _bot_control_payload()
            return jsonify({"ok": True, "asset": asset, "action": action, **payload})
        if action not in ("pause", "resume", "start"):
            return jsonify({"error": "action must be pause, resume, start, or release_daily_stop"}), 400
        paused = action == "pause"
        set_bot_paused(asset, paused)
        payload = _bot_control_payload()
        return jsonify({
            "ok": True,
            "asset": asset,
            "paused": paused,
            **payload,
        })
    return jsonify(_bot_control_payload())


def _daily_stop_block(*, pnl: float, limit: float, effective_hit: bool, status: dict[str, Any]) -> dict[str, Any]:
    released = bool((status.get("daily_stop") or {}).get("released"))
    raw_hit = pnl <= -abs(limit)
    return {
        **(status.get("daily_stop") or {}),
        "pnl": round(float(pnl), 2),
        "limit": round(float(limit), 2),
        "hit": raw_hit,
        "effective": bool(effective_hit),
        "released": released,
    }


def _alpha_daily_stop_block(status: dict[str, Any]) -> dict[str, Any]:
    rows = _load_json_file(ROOT / "logs" / "bot2" / "alpha_max_trades_live.json", [])
    rows = rows if isinstance(rows, list) else []
    today = datetime.now(timezone.utc).astimezone(HKT).strftime("%Y-%m-%d")
    pnl = round(sum(
        float(r.get("pnl") or 0)
        for r in rows
        if r.get("trade_result") in ("WIN", "LOSS")
        and str(r.get("timestamp_hkt") or "").startswith(today)
    ), 2)
    try:
        from alpha_max_live import daily_loss_limit_usd, daily_loss_limit_hit

        limit = float(daily_loss_limit_usd())
    except Exception:
        limit = 75.0
    released = bool((status.get("daily_stop") or {}).get("released"))
    effective_hit = pnl <= -limit and not released
    return _daily_stop_block(pnl=pnl, limit=limit, effective_hit=effective_hit, status=status)


def _bot_control_payload() -> dict[str, Any]:
    status = bot_control_status()
    try:
        combined_pnl = v1_combined_daily_pnl()
        combined_limit = v1_combined_daily_loss_limit_usd()
        combined_block = _daily_stop_block(
            pnl=combined_pnl,
            limit=combined_limit,
            effective_hit=v1_combined_daily_loss_limit_hit(),
            status={"daily_stop": _daily_stop_release_status(load_bot_control(), "v1")},
        )
        status["btc"]["daily_stop"] = combined_block
        status["eth"]["daily_stop"] = combined_block
    except Exception:
        try:
            btc_rows = _filter_strategy_rows(load_active_strategy_log())
            btc_pnl = package2_daily_realized_pnl_hkt(btc_rows)
            btc_limit = float(package2_config("btc")["daily_loss_limit_usd"])
            status["btc"]["daily_stop"] = _daily_stop_block(
                pnl=btc_pnl,
                limit=btc_limit,
                effective_hit=btc_daily_loss_limit_hit(btc_rows, asset="btc"),
                status=status["btc"],
            )
        except Exception:
            pass
        try:
            eth_rows = _filter_session_rows(load_active_eth_log())
            eth_pnl = package2_daily_realized_pnl_hkt(eth_rows)
            eth_limit = float(package2_config("eth")["daily_loss_limit_usd"])
            status["eth"]["daily_stop"] = _daily_stop_block(
                pnl=eth_pnl,
                limit=eth_limit,
                effective_hit=eth_daily_loss_limit_hit(eth_rows),
                status=status["eth"],
            )
        except Exception:
            pass
    try:
        status["alpha_max"]["daily_stop"] = _alpha_daily_stop_block(status["alpha_max"])
    except Exception:
        pass
    return status


@app.route("/api/markets-live")
def api_markets_live():
    """BTC + ETH Polymarket window in one call (short TTL cache, low overhead)."""
    import time

    now = time.time()
    cached = _markets_live_cache.get("data")
    if cached and (now - float(_markets_live_cache.get("ts") or 0)) < MARKETS_LIVE_TTL_SEC:
        return jsonify(cached)
    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "btc": _live_status("btc"),
        "eth": _live_status("eth"),
    }
    _markets_live_cache["ts"] = now
    _markets_live_cache["data"] = payload
    return jsonify(payload)


@app.route("/api/live")
def api_live():
    return jsonify(_live_status("btc"))


@app.route("/api/session")
def api_session():
    return jsonify(load_live_session())


@app.route("/api/stats")
def api_stats():
    mode = os.getenv("TRADING_MODE", "paper").lower()
    return jsonify({
        "window_stats": read_window_stats(),
        "live": new_strategy_summary(live_wallet=False) if mode == "live" else None,
        "trading_mode": mode,
        "updated": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/eth-summary")
def api_eth_summary():
    return jsonify(eth_summary())


@app.route("/api/crypto15m/summary")
def api_crypto15m_summary():
    from paper_15m_engine import crypto_15m_summary

    return jsonify(crypto_15m_summary())


@app.route("/api/crypto15m/quality")
def api_crypto15m_quality():
    from paper_15m_engine import crypto_15m_quality_report

    return jsonify(crypto_15m_quality_report())


@app.route("/api/dual-leg/summary")
def api_dual_leg_summary():
    from dual_leg_paper import summary

    return jsonify(summary())


@app.route("/api/dual-leg/trades")
def api_dual_leg_trades():
    from dual_leg_paper import recent_trades

    limit = min(int(request.args.get("limit", 30)), 200)
    return jsonify(recent_trades(limit=limit))


@app.route("/api/crypto15m/trades")
def api_crypto15m_trades():
    from paper_15m_engine import recent_trades

    limit = min(int(request.args.get("limit", 30)), 200)
    return jsonify(recent_trades(limit=limit))


def _crypto15m_control_payload() -> dict[str, Any]:
    from paper_15m_engine import (
        crypto_15m_paused,
        crypto_15m_summary,
        crypto_15m_trading_mode,
        load_session,
    )

    summary = crypto_15m_summary()
    sess = load_session()
    mode = crypto_15m_trading_mode()
    return {
        "mode": mode,
        "live": mode == "live",
        "paused": crypto_15m_paused(),
        "running": mode == "live" and not crypto_15m_paused(),
        "session": sess,
        "summary": summary,
    }


@app.route("/api/crypto15m/control", methods=["GET", "POST"])
def api_crypto15m_control():
    import subprocess

    if request.method == "GET":
        return jsonify(_crypto15m_control_payload())

    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).lower()
    env_file = ROOT / ".env"

    def _set_env_key(key: str, value: str) -> None:
        lines = env_file.read_text().splitlines() if env_file.exists() else []
        out: list[str] = []
        found = False
        prefix = f"{key}="
        for line in lines:
            if line.startswith(prefix):
                out.append(f"{key}={value}")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"{key}={value}")
        env_file.write_text("\n".join(out) + "\n")

    if action in ("pause", "resume", "start"):
        from bot_control import set_bot_paused

        paused = action == "pause"
        set_bot_paused("crypto_15m", paused)
        return jsonify({"ok": True, "action": action, **_crypto15m_control_payload()})

    if action == "enable_live":
        py = ROOT / "venv/bin/python3"
        script = ROOT / "scripts/start_crypto_15m_live_session.py"
        subprocess.run([str(py), str(script)], check=False, cwd=str(ROOT))
        return jsonify({"ok": True, "action": action, **_crypto15m_control_payload()})

    if action == "disable_live":
        _set_env_key("CRYPTO_15M_TRADING_MODE", "paper")
        _set_env_key("CRYPTO_15M_DATA_COLLECTION", "1")
        subprocess.run(["systemctl", "restart", "crypto-15m-observer"], check=False)
        return jsonify({"ok": True, "action": action, **_crypto15m_control_payload()})

    return jsonify({"error": "action must be pause, resume, enable_live, or disable_live"}), 400


@app.route("/api/eth-trade-log")
def api_eth_trade_log():
    limit = min(int(request.args.get("limit", 150)), 500)
    backfill_eth_window_decisions()
    rows = _session_rows(load_active_eth_log())
    return jsonify(rows[::-1][:limit])


@app.route("/api/eth-live")
def api_eth_live():
    return jsonify(_live_status("eth"))


def _read_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _top_skip_reasons(counts: dict, n: int = 3) -> list[dict]:
    return [
        {"reason": reason, "count": count}
        for reason, count in sorted(counts.items(), key=lambda item: -item[1])[:n]
    ]


def _rows_since(rows: list[dict], started_at_hkt: str | None) -> list[dict]:
    if not started_at_hkt:
        return rows
    return [r for r in rows if (r.get("timestamp_hkt") or "") >= started_at_hkt]


def _normalize_skip_label(reason: str) -> str:
    text = (reason or "other").strip()
    if text.startswith("Skipped:"):
        text = text.replace("Skipped:", "", 1).strip()
    if text.startswith("Skipped "):
        text = text.replace("Skipped ", "", 1).strip()
    return text[:56] if len(text) > 56 else text


def _package2_log_metrics(path: Path, *, started_at_hkt: str | None) -> dict:
    rows = _rows_since(_read_json_file(path, []), started_at_hkt)
    entered = [r for r in rows if r.get("entered")]
    settled = [
        r
        for r in rows
        if r.get("trade_result") in ("WIN", "LOSS")
        or (r.get("status") == "settled" and r.get("pnl") is not None)
    ]
    skip_counts: Counter[str] = Counter()
    for row in rows:
        if row.get("entered"):
            continue
        if row.get("trade_result") != "SKIPPED" and not row.get("strategy_reason"):
            continue
        skip_counts[_normalize_skip_label(row.get("skip_category") or row.get("strategy_reason") or "other")] += 1
    pnls = [float(r.get("pnl") or 0) for r in settled]
    wins = sum(1 for p in pnls if p >= 0)
    return {
        "trades_entered": len(entered),
        "windows_settled": len(settled),
        "pnl": round(sum(pnls), 2),
        "win_rate": round(100 * wins / len(settled), 1) if settled else None,
        "top_skips": _top_skip_reasons(dict(skip_counts)),
    }


_EXPERIMENT_METRICS = {
    "btc_live": lambda exp: _package2_log_metrics(LIVE_TRADE_LOG_FILE, started_at_hkt=exp.get("started_at_hkt")),
    "eth_live": lambda exp: _package2_log_metrics(ETH_LIVE_TRADE_LOG_FILE, started_at_hkt=exp.get("started_at_hkt")),
}

_EXPERIMENT_KIND_LABELS = {
    "ab_test": "A/B 測試",
    "monitor": "上線監控",
    "rollout": "已部署",
}


def _enrich_experiment(exp: dict) -> dict:
    out = dict(exp)
    source = exp.get("metrics_source")
    if source in _EXPERIMENT_METRICS:
        try:
            out["live_metrics"] = _EXPERIMENT_METRICS[source](exp)
        except Exception:
            out["live_metrics"] = None
    kind = exp.get("kind")
    if kind in _EXPERIMENT_KIND_LABELS:
        out["kind_label"] = _EXPERIMENT_KIND_LABELS[kind]
    return out


def _load_experiments() -> list[dict]:
    path = ROOT / "logs" / "experiments.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        active = [e for e in data.get("experiments", []) if e.get("status") == "active"]
        return [_enrich_experiment(e) for e in active]
    except json.JSONDecodeError:
        return []


@app.route("/api/experiments")
def api_experiments():
    return jsonify({"experiments": _load_experiments()})


@app.route("/api/execution-metrics")
def api_execution_metrics():
    days = min(int(request.args.get("days", 7)), 90)
    return jsonify(execution_metrics(days=days))


@app.route("/api/daily-ops")
def api_daily_ops():
    days = min(int(request.args.get("days", 1)), 7)
    return jsonify(build_daily_ops_report(days=days))


@app.route("/api/weather-summary")
def api_weather_summary():
    return jsonify(safe_weather_summary())


@app.route("/api/weather-trades")
def api_weather_trades():
    limit = min(int(request.args.get("limit", 100)), 200)
    return jsonify(safe_weather_trades(limit))



@app.route("/api/extended-hours-live", methods=["GET", "POST"])
def api_extended_hours_live():
    if request.method == "POST":
        set_btc_extended_mode("us_hours")
        set_extended_hours_live(asset="eth", enabled=False)
        payload = _extended_hours_api_payload(get_extended_hours_live_status())
        payload["message"] = "Extended hours disabled — BTC/ETH trade US regular hours only"
        payload["disabled"] = True
        return jsonify(payload)
    return jsonify(_extended_hours_api_payload(get_extended_hours_live_status()))


def _extended_hours_api_payload(status: dict, toggled: str | None = None, btc_mode: str | None = None) -> dict:
    """Extended hours toggles session window only — never live/paper mode."""
    out = dict(status)
    btc_mode = os.getenv("TRADING_MODE", "paper").lower()
    eth_mode = os.getenv("ETH_TRADING_MODE", "paper").lower()
    out["btc_trading_mode"] = btc_mode
    out["eth_trading_mode"] = eth_mode
    out["live_unchanged"] = True
    btc = out.get("btc") or {}
    eth = out.get("eth") or {}
    if not btc.get("enabled") and not eth.get("enabled"):
        out["message"] = "Extended hours disabled — BTC/ETH trade US regular hours only"
    elif toggled == "btc":
        mode = btc_mode or (btc.get("mode") or "us_hours")
        if mode == "extended_dog_only":
            out["message"] = "BTC Extended LIVE · Dog Only — non-US hours underdog entries only"
        elif mode == "extended" or btc.get("enabled"):
            out["message"] = "BTC Extended LIVE ON — non-US hours trading enabled (fav + dog)"
        else:
            out["message"] = "BTC US Hours · LIVE — trades only during US regular session"
    elif toggled == "eth":
        out["message"] = (
            "ETH Extended LIVE ON — non-US hours trading enabled"
            if eth.get("enabled")
            else "ETH US Hours · LIVE — trades only during US regular session"
        )
    else:
        out["message"] = (
            f"BTC {'Extended' if btc.get('enabled') else 'US Hours'} · "
            f"ETH {'Extended' if eth.get('enabled') else 'US Hours'} · both stay LIVE"
        )
    return out


@app.route("/api/sports-clv-summary")
def api_sports_clv_summary():
    from alpha_max import alpha_max_summary

    return jsonify(alpha_max_summary())


@app.route("/api/sports-clv-trades")
def api_sports_clv_trades():
    from alpha_max import load_alpha_trades

    limit = min(int(request.args.get("limit", 100)), 200)
    return jsonify(load_alpha_trades()[::-1][:limit])


@app.route("/api/sports-clv-scan-latest")
def api_sports_clv_scan_latest():
    from alpha_max import alpha_max_summary

    return jsonify({"ok": True, "top_signals": [], "stats": alpha_max_summary()})


@app.route("/api/sports-clv-scan")
def api_sports_clv_scan():
    from alpha_max import alpha_max_summary

    return jsonify({"ok": True, "top_signals": [], "stats": alpha_max_summary()})


@app.route("/api/alpha-max-research")
def api_alpha_max_research():
    from alpha_max_research import build_research_report

    refresh = request.args.get("refresh", "0") == "1"
    if refresh:
        from alpha_max_research import backfill_research_from_windows

        backfill_research_from_windows()
    return jsonify(build_research_report(refresh_cache=True))


@app.route("/api/alpha-max-go-live")
def api_alpha_max_go_live():
    from alpha_max_live import alpha_max_live_summary

    try:
        return jsonify(alpha_max_live_summary())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/alpha-max-live")
def api_alpha_max_live():
    try:
        from competition_dashboard import build_alpha_live_dashboard

        return jsonify(build_alpha_live_dashboard())
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/")
def index():
    return render_template("v1_only.html")


@app.route("/manual")
@app.route("/manual/")
def v1_manual():
    return redirect("/")


@app.route("/logs")
@app.route("/logs/")
def v1_logs():
    config = _load_config()
    eth = eth_summary()
    eth_rules = eth.get("eth_rules") or {}
    extended_hours = get_extended_hours_live_status()
    strategy_display = build_strategy_display(
        config,
        extended_hours,
        entry_window=f"T-{ENTRY_T_MAX} → T-{ENTRY_T_MIN}",
        eth_rules=eth_rules,
        btc_live=(os.getenv("TRADING_MODE", "paper").lower() == "live"),
        eth_live=(eth_trading_mode().lower() == "live"),
    )
    from package2_strategy import v1_stake_plan_snapshot

    return render_template(
        "v1_detail.html",
        strategy_display=strategy_display,
        v1_risk=config.get("v1_risk") or {},
        v1_scaling=config.get("v1_stake_scaling") or {},
        stake_plan=v1_stake_plan_snapshot(),
    )


@app.route("/simple")
@app.route("/simple/")
@app.route("/trading")
@app.route("/trading/")
@app.route("/eth")
@app.route("/eth/")
@app.route("/weather")
@app.route("/weather/")
@app.route("/legacy")
@app.route("/legacy/")
def split_legacy():
    """Serve legacy dashboard (avoid redirect loop if browser cached / → /simple)."""
    return index()


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8083"))
    app.run(host="0.0.0.0", port=port, debug=False)
