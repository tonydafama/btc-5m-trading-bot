#!/usr/bin/env python3
"""Paper daemon for 先有波幅，先買中間價 (Mid-band Range80).

BTC 5-minute Up/Down only. One side per window. No hedge, no flip.
Holds to official Polymarket settlement. Never posts a CLOB order.

This process forces DRY_RUN=1 / REAL_MONEY=0 / TRADING_MODE=paper in
its own environment and exits if config/midband_range80_paper.env asks
for live. It does not read or rewrite the shared droplet .env, and it
does not start or stop any other unit.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(os.getenv("MIDBAND_BOT_ROOT") or "/root/btc-5m-bot")
if not ROOT.is_dir():
    ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from midband_range80 import (  # noqa: E402
    SESSION_NAME,
    SESSION_PREFIX,
    STATE_NAME,
    STATUS_NAME,
    STRATEGY_ID,
    STRATEGY_NAME,
    TRADES_NAME,
    UNIT_USD,
    assert_paper_env,
    best_ask,
    force_paper_process_env,
    paper_fok_fill,
    decide_snapshot,
    previous_completed_range_usd,
)
from polymarket_btc import (  # noqa: E402
    discover_current_market,
    fetch_market_outcome,
    fetch_order_book,
    seconds_to_end,
    summarize_book,
)
from price_feeds import fetch_binance_klines  # noqa: E402

HKT = ZoneInfo("Asia/Hong_Kong")
ENV_FILE = ROOT / "config" / "midband_range80_paper.env"
LOG_DIR = ROOT / "logs" / "sons"
_STOP = False


def now_hkt() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def _handle(signum, _frame) -> None:  # noqa: ANN001
    global _STOP
    _STOP = True
    print(f"[midband] signal {signum} — stopping @ {now_hkt()}", flush=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_own_env() -> str:
    """Load only this strategy's paper env. Never the shared .env."""
    if not ENV_FILE.is_file():
        return ""
    text = ENV_FILE.read_text(encoding="utf-8")
    assert_paper_env(text)
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in {"DRY_RUN", "REAL_MONEY", "TRADING_MODE", "DUAL_LEG_LIVE_DRY_RUN"}:
            continue
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))
    return text


def _enrich(raw: dict) -> dict:
    out = summarize_book(raw) or {}
    asks = raw.get("asks") or raw.get("sells") or []
    if isinstance(asks, list) and asks:
        out["asks"] = asks
    return out


def _range_usd(now_ms: int) -> float | None:
    try:
        klines = fetch_binance_klines("5m", 3)
    except Exception as exc:
        print(f"[midband] binance 5m klines failed: {exc}", flush=True)
        return None
    return previous_completed_range_usd(klines, now_ms)


def settle_trades(trades: list[dict]) -> tuple[list[dict], int, float]:
    changed = 0
    out: list[dict] = []
    for row in trades:
        if row.get("settled_at_hkt") and row.get("pnl") is not None:
            out.append(row)
            continue
        if not row.get("entered"):
            out.append(row)
            continue
        slug = str(row.get("slug") or "")
        if not slug:
            out.append(row)
            continue
        try:
            pm = fetch_market_outcome(slug)
        except Exception as exc:
            print(f"[midband] settle fetch fail {slug}: {exc}", flush=True)
            out.append(row)
            continue
        if not pm or not pm.get("outcome"):
            out.append(row)
            continue
        outcome = str(pm["outcome"]).lower()
        side = str(row.get("side") or "").lower()
        stake = float(row.get("stake") or 0)
        shares = float(row.get("shares") or 0)
        fee = float(row.get("estimated_taker_fee") or 0)
        won = side == outcome
        payout = shares if won else 0.0
        gross = payout - stake
        net = gross - fee
        row = dict(row)
        row.update(
            {
                "outcome": outcome.upper(),
                "trade_result": "WIN" if net > 0.01 else ("BREAKEVEN" if abs(net) < 0.01 else "LOSS"),
                "gross_pnl": round(gross, 2),
                "pnl": round(net, 2),
                "pnl_usd": round(net, 2),
                "settled_at_hkt": now_hkt(),
                "settlement_source": "polymarket",
                "held_to_settlement": True,
                "hedged": False,
            }
        )
        changed += 1
        print(f"[midband] SETTLED {slug} {row['trade_result']} pnl=${row['pnl']}", flush=True)
        out.append(row)
    realized = 0.0
    for row in out:
        if row.get("entered") and row.get("pnl") is not None:
            try:
                realized += float(row["pnl"])
            except (TypeError, ValueError):
                pass
    return out, changed, round(realized, 2)


def _session_rows(trades: list[dict], session_id: str) -> list[dict]:
    return [r for r in trades if r.get("session_id") == session_id]


def run_forever(poll_sec: float) -> int:
    force_paper_process_env()
    _load_own_env()
    force_paper_process_env()
    assert_paper_env(ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.is_file() else "")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    trades_path = LOG_DIR / TRADES_NAME
    status_path = LOG_DIR / STATUS_NAME
    state_path = LOG_DIR / STATE_NAME
    session_path = LOG_DIR / SESSION_NAME

    session = _read_json(session_path, {})
    session_id = str(session.get("session_id") or "")
    if not session_id.startswith(SESSION_PREFIX):
        stamp = datetime.now(HKT).strftime("%Y%m%d-%H%M%S")
        session_id = f"{SESSION_PREFIX}-{stamp}"
        session = {
            "session_id": session_id,
            "strategy": STRATEGY_ID,
            "display_name": STRATEGY_NAME,
            "started_at_hkt": now_hkt(),
            "mode": "paper",
            "dry_run": True,
            "real_money": False,
            "unit_usd": UNIT_USD,
            "capital_usd": None,
            "capital_label": "待入金",
            "note": "Clean paper session. No live orders. Capital waits for a deposit.",
        }
        _write_json(session_path, session)
        if not trades_path.exists():
            _write_json(trades_path, [])

    trades = _read_json(trades_path, [])
    if not isinstance(trades, list):
        trades = []
    state = _read_json(state_path, {})
    current_slug = state.get("current_slug")
    decided = set(state.get("decided_slugs") or [])
    saw_window = bool(state.get("saw_entry_window"))
    last_wait = str(state.get("last_wait_reason") or "")
    started = session.get("started_at_hkt") or now_hkt()
    entries = 0
    skips = 0
    errors = 0
    samples = 0

    print(
        f"[midband] START PAPER {STRATEGY_NAME} session={session_id} "
        f"unit=${UNIT_USD:.0f} DRY_RUN=1 REAL_MONEY=0 TRADING_MODE=paper",
        flush=True,
    )

    while not _STOP:
        try:
            force_paper_process_env()
            market = discover_current_market()
            if market is None:
                time.sleep(poll_sec)
                continue
            t_rem = float(seconds_to_end(market))
            if market.slug != current_slug:
                if current_slug and current_slug not in decided and saw_window:
                    trades.append(
                        {
                            "strategy": STRATEGY_ID,
                            "display_name": STRATEGY_NAME,
                            "session_id": session_id,
                            "mode": "PAPER",
                            "slug": current_slug,
                            "entered": False,
                            "trade_result": "SKIP",
                            "stake": 0.0,
                            "hedged": False,
                            "strategy_reason": last_wait or "no entry before window rolled",
                            "skip_category": "window_elapsed",
                            "logged_at_hkt": now_hkt(),
                            "paper": True,
                            "dry_run": True,
                            "posted_live_order": False,
                        }
                    )
                    decided.add(current_slug)
                    skips += 1
                    _write_json(trades_path, trades)
                current_slug = market.slug
                saw_window = False
                last_wait = ""
                print(f"[midband] window {market.slug} t_rem={t_rem:.1f}", flush=True)

            if samples % 8 == 0:
                trades, settled_n, _ = settle_trades(trades)
                if settled_n:
                    _write_json(trades_path, trades)

            if current_slug not in decided and 40.0 <= t_rem <= 75.0:
                saw_window = True
                samples += 1
                up_book = _enrich(fetch_order_book(market.up_token_id))
                down_book = _enrich(fetch_order_book(market.down_token_id))
                range_usd = _range_usd(int(time.time() * 1000))
                decision = decide_snapshot(
                    t_remaining=t_rem,
                    range_usd=range_usd,
                    up_book=up_book,
                    down_book=down_book,
                )
                action = decision.get("action")
                if action == "wait":
                    last_wait = str(decision.get("reason") or "")
                elif action == "skip_window":
                    trades.append(
                        {
                            "strategy": STRATEGY_ID,
                            "display_name": STRATEGY_NAME,
                            "session_id": session_id,
                            "mode": "PAPER",
                            "slug": market.slug,
                            "title": market.title,
                            "entered": False,
                            "trade_result": "SKIP",
                            "stake": 0.0,
                            "hedged": False,
                            "t_remaining": round(t_rem, 1),
                            "range_usd": decision.get("range_usd"),
                            "up_ask": decision.get("up_ask"),
                            "down_ask": decision.get("down_ask"),
                            "strategy_reason": decision.get("reason"),
                            "skip_category": decision.get("skip_category"),
                            "logged_at_hkt": now_hkt(),
                            "paper": True,
                            "dry_run": True,
                            "posted_live_order": False,
                        }
                    )
                    decided.add(market.slug)
                    skips += 1
                    _write_json(trades_path, trades)
                elif action == "enter":
                    side = str(decision.get("side") or "")
                    book = up_book if side == "up" else down_book
                    fill = paper_fok_fill(book, unit_usd=UNIT_USD)
                    if not fill.get("filled"):
                        last_wait = f"paper FOK no fill: {fill.get('no_fill_reason')}"
                    else:
                        fill_ask = float(fill["fill_price"])
                        fill_stake = float(fill["stake"])
                        shares = float(fill["shares"])
                        fee = float(fill["estimated_taker_fee"])
                        row = {
                            "strategy": STRATEGY_ID,
                            "display_name": STRATEGY_NAME,
                            "session_id": session_id,
                            "mode": "PAPER",
                            "slug": market.slug,
                            "title": market.title,
                            "side": side,
                            "ask": decision.get("ask"),
                            "entry_ask": fill_ask,
                            "decision_ask": decision.get("ask"),
                            "up_ask": best_ask(up_book),
                            "down_ask": best_ask(down_book),
                            "t_remaining": round(t_rem, 1),
                            "range_usd": decision.get("range_usd"),
                            "entered": True,
                            "filled": True,
                            "hedged": False,
                            "legs": 1,
                            "trade_result": "OPEN",
                            "unit_usd": UNIT_USD,
                            "stake": round(fill_stake, 2),
                            "stake_floor_applied": bool(fill.get("stake_floor_applied")),
                            "shares": round(shares, 6),
                            "estimated_taker_fee": fee,
                            "strategy_reason": decision.get("reason"),
                            "execution_path": fill.get("execution_path") or "PAPER_FOK_SIM",
                            "paper_fill_model": "live_style_fok",
                            "opened_at_hkt": now_hkt(),
                            "logged_at_hkt": now_hkt(),
                            "paper": True,
                            "dry_run": True,
                            "posted_live_order": False,
                            "hold_to_settlement": True,
                        }
                        trades.append(row)
                        decided.add(market.slug)
                        entries += 1
                        _write_json(trades_path, trades)
                        print(
                            f"[midband] PAPER ENTRY {market.slug} {side} @ {fill_ask:.3f} "
                            f"stake=${fill_stake:.2f} (unit ${UNIT_USD:.0f})",
                            flush=True,
                        )
            elif current_slug not in decided and saw_window and t_rem < 40.0:
                trades.append(
                    {
                        "strategy": STRATEGY_ID,
                        "display_name": STRATEGY_NAME,
                        "session_id": session_id,
                        "mode": "PAPER",
                        "slug": market.slug,
                        "title": market.title,
                        "entered": False,
                        "trade_result": "SKIP",
                        "stake": 0.0,
                        "hedged": False,
                        "t_remaining": round(t_rem, 1),
                        "strategy_reason": last_wait or "no mid-band entry before T-40",
                        "skip_category": "entry_window_closed",
                        "logged_at_hkt": now_hkt(),
                        "paper": True,
                        "dry_run": True,
                        "posted_live_order": False,
                    }
                )
                decided.add(market.slug)
                skips += 1
                _write_json(trades_path, trades)

            session_trades = _session_rows(trades, session_id)
            _, _, realized = settle_trades(session_trades)
            trade_count = sum(1 for r in session_trades if r.get("entered"))
            status = {
                "id": STRATEGY_ID,
                "display_name": STRATEGY_NAME,
                "display_name_en": "Mid-band Range80",
                "armed": False,
                "launched": True,
                "status": "RUNNING · paper",
                "status_label": "跑緊 · Paper / prep · 真倉 OFF",
                "mode": "paper",
                "live_enabled": False,
                "live_on_air": False,
                "dry_run": True,
                "real_money": False,
                "DRY_RUN": "1",
                "REAL_MONEY": "0",
                "TRADING_MODE": "paper",
                "DUAL_LEG_LIVE_DRY_RUN": "1",
                "posted_live_orders": 0,
                "capital_usd": None,
                "capital_label": "待入金",
                "funded": False,
                "unit_usd": UNIT_USD,
                "pnl": realized,
                "pnl_usd": realized,
                "trade_count": trade_count,
                "entries": entries,
                "skips": skips,
                "samples": samples,
                "errors": errors,
                "session_id": session_id,
                "heartbeat_at_hkt": now_hkt(),
                "started_at_hkt": started,
                "current_slug": current_slug,
                "hedge": False,
                "one_trade_per_window": True,
                "note": "Paper / prep only. This process cannot post a live order.",
            }
            _write_json(status_path, status)
            _write_json(
                state_path,
                {
                    "current_slug": current_slug,
                    "decided_slugs": list(decided)[-400:],
                    "saw_entry_window": saw_window,
                    "last_wait_reason": last_wait,
                    "session_id": session_id,
                },
            )
            near_entry = current_slug is not None and 35.0 <= t_rem <= 80.0
            time.sleep(1.0 if near_entry else poll_sec)
        except Exception as exc:
            errors += 1
            print(f"[midband] loop error: {exc}", flush=True)
            time.sleep(poll_sec)

    print(f"[midband] stopped @ {now_hkt()}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Mid-band Range80 PAPER daemon")
    ap.add_argument("--poll-sec", type=float, default=2.0)
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    return run_forever(max(1.0, float(args.poll_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
