#!/usr/bin/env python3
"""BTC dual-leg LIVE daemon — profile from DUAL_LEG_LIVE_PROFILE (v1|v2), UNIT from env, posts FOK when DRY_RUN=0.

Safety:
- Requires DUAL_LEG_LIVE_DRY_RUN=0 (refuses to start otherwise).
- BTC only (dual_leg_live.assert_btc_only).
- Profile via DUAL_LEG_LIVE_PROFILE (v2 for go-live prep unit dual-leg-live-v2).
- Shadow service stays separate and forces DRY_RUN=1 — this daemon alone posts.
- Does NOT stop observers / paper bots.
- No max-concurrent-pairs gate (user declined).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path("/root/btc-5m-bot")
sys.path.insert(0, str(ROOT))

# Do NOT force DRY_RUN here — live path needs 0. Shadow forces 1 in its own process.
from dual_leg_live import (  # noqa: E402
    FokOrderIntent,
    PROFILES,
    assert_btc_only,
    build_fok_buy_intent,
    dry_run_enabled,
    evaluate_checkpoint,
    entry_pause_view,
    execute_fok_intent,
    live_profile_name,
    live_unit_usd,
    new_entry_paused,
    now_hkt,
    read_live_unit_usd,
    profile_to_dict,
    account_live_trade_pnl,
)
from polymarket_fees import estimate_taker_fee  # noqa: E402

LOG_DIR = ROOT / "logs"
# Paths overridable so live-shadow can isolate from parent live files.
STATE_PATH = Path(os.getenv("DUAL_LEG_LIVE_STATE_PATH") or (LOG_DIR / "dual_leg_live_state.json"))
TRADES_PATH = Path(os.getenv("DUAL_LEG_LIVE_TRADES_PATH") or (LOG_DIR / "dual_leg_live_trades.json"))
STATUS_PATH = Path(os.getenv("DUAL_LEG_LIVE_STATUS_PATH") or (LOG_DIR / "dual_leg_live_status.json"))
EVENT_LOG = Path(os.getenv("DUAL_LEG_LIVE_EVENT_LOG") or (LOG_DIR / "dual_leg_live_daemon.jsonl"))

_STOP = False


def _handle_signal(signum, _frame) -> None:  # noqa: ANN001
    global _STOP
    _STOP = True
    print(f"[live] signal {signum} — stopping", flush=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(path)


def _append_event(row: dict[str, Any]) -> None:
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a") as handle:
        handle.write(json.dumps(row, default=str) + "\n")



def _leg_list(row: dict[str, Any]) -> list[dict[str, Any]]:
    legs: list[dict[str, Any]] = []
    first = row.get("first_leg")
    second = row.get("second_leg")
    if isinstance(first, dict):
        legs.append(first)
    if isinstance(second, dict):
        legs.append(second)
    return legs


def _settle_trade_row(row: dict[str, Any], outcome: str) -> dict[str, Any]:
    """Mark a live trade closed from official Up/Down outcome (net of taker fees)."""
    return account_live_trade_pnl(row, outcome)


def _refresh_settled_fees(trades: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Re-account closed rows that were settled before per-leg fees were stored."""
    changed = 0
    out: list[dict[str, Any]] = []
    for row in trades:
        if row.get("pnl") is None or not row.get("outcome"):
            out.append(row)
            continue
        old_pnl = row.get("pnl")
        old_fee = float(row.get("total_estimated_fee") or 0)
        refreshed = account_live_trade_pnl(dict(row), str(row["outcome"]).lower())
        new_fee = float(refreshed.get("total_estimated_fee") or 0)
        if abs(new_fee - old_fee) > 1e-6 or refreshed.get("pnl") != old_pnl:
            changed += 1
        out.append(refreshed)
    return out, changed


def settle_pending_live_trades(trades: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, float]:
    """Resolve window_rolled / open-ended live rows once Polymarket publishes outcome."""
    from polymarket_window import fetch_market_outcome

    trades, fee_refresh = _refresh_settled_fees(trades)
    changed = fee_refresh
    out: list[dict[str, Any]] = []
    for row in trades:
        already = row.get("settled_at_hkt") and row.get("pnl") is not None
        if already:
            out.append(row)
            continue
        slug = str(row.get("slug") or "")
        if not slug:
            out.append(row)
            continue
        # Only settle rows that already left the live window (or still marked hedged/one_leg)
        reason = str(row.get("close_reason") or "")
        status = str(row.get("status") or "")
        if status == "closed" and row.get("pnl") is not None:
            out.append(row)
            continue
        if reason not in ("window_rolled_pending_outcome", "settled_official_outcome", "") and status not in (
            "hedged",
            "one_leg",
            "window_rolled",
            "pending_outcome",
        ):
            # still actively managed elsewhere
            if status not in ("hedged", "one_leg", "window_rolled", "pending_outcome"):
                out.append(row)
                continue
        try:
            pm = fetch_market_outcome(slug)
        except Exception as exc:
            print(f"[live] settle fetch fail {slug}: {exc}", flush=True)
            out.append(row)
            continue
        if not pm or not pm.get("outcome"):
            out.append(row)
            continue
        settled = _settle_trade_row(row, str(pm["outcome"]))
        if settled.get("settled_at_hkt") and settled.get("pnl") is not None and not already:
            changed += 1
            print(
                f"[live] SETTLED {slug} outcome={settled.get('outcome')} "
                f"pnl=${settled.get('pnl')} fee=${settled.get('total_estimated_fee')}",
                flush=True,
            )
            _append_event(
                {
                    "event": "trade_settled",
                    "timestamp_hkt": now_hkt(),
                    "slug": slug,
                    "outcome": settled.get("outcome"),
                    "pnl": settled.get("pnl"),
                    "trade_result": settled.get("trade_result"),
                }
            )
        out.append(settled)
    realized = 0.0
    for row in out:
        if row.get("pnl") is not None:
            try:
                realized += float(row["pnl"])
            except (TypeError, ValueError):
                pass
    return out, changed, round(realized, 2)


def enrich_book_with_asks(summary: dict, raw: dict) -> dict:
    out = dict(summary or {})
    asks = raw.get("asks") or raw.get("sells") or []
    if isinstance(asks, list) and asks and "asks" not in out:
        out["asks"] = asks
    return out


def _wallet_cash() -> float | None:
    try:
        from polymarket_wallet import fetch_wallet_status

        w = fetch_wallet_status(force=True)
        if isinstance(w, dict) and w.get("cash_usdc") is not None:
            cash = float(w["cash_usdc"])
            # refresh dashboard cache
            cache = LOG_DIR / "wallet_cache.json"
            payload = dict(w)
            from datetime import datetime
            from zoneinfo import ZoneInfo

            payload["updated_at_hkt"] = datetime.now(ZoneInfo("Asia/Hong_Kong")).strftime(
                "%Y-%m-%d %H:%M:%S HKT"
            )
            payload["fetched_at_hkt"] = payload["updated_at_hkt"]
            _write_json(cache, payload)
            return cash
    except Exception as exc:
        print(f"[live] wallet refresh error: {exc}", flush=True)
    return None


def _leg_from_fill(
    side: str,
    intent: FokOrderIntent,
    result: dict[str, Any],
    checkpoint: str | None,
    t_remaining: float,
) -> dict[str, Any]:
    order = result.get("order") or {}
    stake = float(order.get("stake_usd") or intent.stake_usd)
    price = float(
        order.get("live_ask_at_fill")
        or order.get("decision_ask")
        or intent.decision_ask
    )
    shares = stake / price if price > 0 else 0.0
    fee = estimate_taker_fee(shares, price) if shares > 0 and price > 0 else 0.0
    return {
        "side": side,
        "ask": round(price, 4),
        "stake": round(stake, 2),
        "shares": round(shares, 6),
        "estimated_taker_fee": round(fee, 4),
        "checkpoint": checkpoint,
        "t_remaining": t_remaining,
        "posted_at_hkt": now_hkt(),
        "order_id": order.get("orderID") or order.get("order_id"),
        "execution_path": order.get("execution_path") or "FOK",
        "raw_status": result.get("status"),
    }


def _post_intent(intent: FokOrderIntent) -> dict[str, Any]:
    return execute_fok_intent(intent, force_live=True)


def run_forever(poll_sec: float) -> int:
    from polymarket_btc import (
        discover_current_market,
        fetch_order_book,
        seconds_to_end,
        summarize_book,
    )
    from price_feeds import fetch_btc_usd, recent_market_regime

    assert_btc_only("btc")

    if dry_run_enabled() or os.getenv("DUAL_LEG_LIVE_DRY_RUN", "1") != "0":
        print("REFUSING: DUAL_LEG_LIVE_DRY_RUN must be 0 for live daemon", file=sys.stderr)
        return 2

    profile_name = live_profile_name("v1")
    profile = PROFILES[profile_name]
    unit = live_unit_usd(6.0)

    cash = _wallet_cash()
    if cash is None:
        print("REFUSING: cannot read wallet cash", file=sys.stderr)
        return 2
    if cash < unit:
        print(
            f"REFUSING: cash ${cash:.2f} < one leg ${unit:.2f}",
            file=sys.stderr,
        )
        return 2

    state = _read_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    trades = _read_json(TRADES_PATH, [])
    if not isinstance(trades, list):
        trades = []

    posted_live_orders = 0
    for row in trades:
        if isinstance(row, dict):
            posted_live_orders += int(row.get("legs_posted") or 0)
    for row in state.values():
        if isinstance(row, dict):
            posted_live_orders += int(row.get("legs_posted") or 0)

    started = now_hkt()
    samples = 0
    errors = 0
    last_slug = None
    open_price = None
    paused_logged: set[str] = set()

    _append_event(
        {
            "event": "live_daemon_start",
            "timestamp_hkt": started,
            "dry_run": False,
            "profile": profile_name,
            "unit_usd": unit,
            "pair_lock_usd": round(unit * 2, 2),
            "wallet_cash_usdc": cash,
            "profile_params": profile_to_dict(profile),
            "note": f"{profile_name.upper()} live FOK daemon · BTC only · no max-open gate",
        }
    )
    print(
        f"[live] START DRY_RUN=0 profile={profile_name} unit=${unit} "
        f"cash=${cash:.2f} poll={poll_sec}s @ {started}",
        flush=True,
    )

    while not _STOP:
        if dry_run_enabled() or os.getenv("DUAL_LEG_LIVE_DRY_RUN", "1") != "0":
            print("REFUSING mid-loop: DRY_RUN flipped back to 1", file=sys.stderr)
            return 3

        try:
            market = discover_current_market()
            if not market:
                time.sleep(poll_sec)
                continue
            t_rem = float(seconds_to_end(market))
            if last_slug != market.slug:
                # Finalize previous open row into trades if window rolled
                if last_slug and last_slug in state:
                    old = state.pop(last_slug)
                    old["status"] = old.get("status") or "window_rolled"
                    old["closed_at_hkt"] = now_hkt()
                    old["close_reason"] = "window_rolled_pending_outcome"
                    trades.append(old)
                    _write_json(TRADES_PATH, trades)
                    _write_json(STATE_PATH, state)
                last_slug = market.slug
                open_price = None
                print(f"[live] window {market.slug} t_rem={t_rem:.1f}", flush=True)

            btc = fetch_btc_usd()
            px = float(btc["price"])
            if open_price is None:
                open_price = px
            delta_usd = px - open_price
            delta_pct = (delta_usd / open_price * 100) if open_price else 0.0

            up_raw = fetch_order_book(market.up_token_id)
            down_raw = fetch_order_book(market.down_token_id)
            up_book = enrich_book_with_asks(summarize_book(up_raw), up_raw)
            down_book = enrich_book_with_asks(summarize_book(down_raw), down_raw)
            regime = recent_market_regime("btc")

            cp = None
            for label in (75, 60, 50, 40, 30, 25, 12, 5):
                if abs(t_rem - label) <= 2.5:
                    cp = f"T-{label}"
                    break

            snapshot = {
                "slug": market.slug,
                "checkpoint": cp or f"T-{int(t_rem)}",
                "t_remaining": round(t_rem, 1),
                "delta_usd": round(delta_usd, 2),
                "delta_pct": round(delta_pct, 4),
                "up_book": up_book,
                "down_book": down_book,
                "binance_regime": regime,
            }
            tokens = {"up": market.up_token_id, "down": market.down_token_id}

            # A desk bet-size save updates the exec env file. Use it on the next
            # fresh window. An open pair keeps the stake already stored on the row
            # so the second leg matches the first.
            open_row = state.get(market.slug)
            if open_row and open_row.get("unit_usd") not in (None, ""):
                try:
                    unit = float(open_row["unit_usd"])
                except (TypeError, ValueError):
                    unit = read_live_unit_usd(unit)
            else:
                unit = read_live_unit_usd(unit)

            ev = evaluate_checkpoint(
                asset="btc",
                snapshot=snapshot,
                profile=profile,
                unit_override=unit,
                token_ids=tokens,
            )
            samples += 1
            row = state.get(market.slug)

            # --- Entry: no open position ---
            # 18:00–24:00 HKT blocks a new first leg only. A one-leg row still hedges.
            _block_entry_pause = False
            if row is None and new_entry_paused():
                _block_entry_pause = True
                if market.slug not in paused_logged:
                    paused_logged.add(market.slug)
                    regime = ev.get("binance_regime") or ev.get("entry_regime") or {}
                    if not isinstance(regime, dict):
                        regime = {}
                    leg1 = ev.get("would_leg1") if isinstance(ev.get("would_leg1"), dict) else None
                    _append_event(
                        {
                            "event": "skip_entry_hkt_pause",
                            "timestamp_hkt": now_hkt(),
                            "slug": market.slug,
                            "window_hkt": "18:00-24:00",
                            "resume_hkt": "00:00",
                            "would_enter_leg1": bool(ev.get("would_enter_leg1")),
                            "would_leg1_side": leg1.get("side") if leg1 else None,
                            "regime": regime.get("regime"),
                            "skip_reasons": ev.get("skip_reasons") or [],
                            "study": "v2_hkt_pause",
                        }
                    )
                    print(
                        f"[live] PAUSE no new entry {market.slug} HKT 18:00-24:00",
                        flush=True,
                    )
            _block_opp_before = False
            if (
                row is None
                and ev.get("would_enter_leg1")
                and ev.get("would_leg1")
                and not _block_entry_pause
            ):
                # live 影武者 / Son F: optional opp-before gate (default OFF → parent live unchanged)
                _req_opp = (os.getenv("REQUIRE_OPP_BEFORE_LEG1") or "0").strip().lower() in {
                    "1", "true", "yes", "on"
                }
                if _req_opp:
                    _opp_max = float(os.getenv("OPP_BEFORE_LEG1_MAX") or 0.50)
                    _leg1 = ev["would_leg1"]
                    _side1 = str(_leg1["side"])
                    _opp_side = "down" if _side1 == "up" else "up"
                    _opp_book = up_book if _opp_side == "up" else down_book
                    _opp_ask = _opp_book.get("best_ask")
                    if _opp_ask is None or float(_opp_ask) > _opp_max + 1e-9:
                        # REQUIRE_OPP_BEFORE_LEG1 live-shadow gate (do NOT continue — settle/status still run)
                        _block_opp_before = True
                        _append_event(
                            {
                                "event": "skip_opp_before_leg1",
                                "timestamp_hkt": now_hkt(),
                                "slug": market.slug,
                                "opp_ask": _opp_ask,
                                "opp_before_max": _opp_max,
                            }
                        )
            if (
                row is None
                and ev.get("would_enter_leg1")
                and ev.get("would_leg1")
                and not _block_opp_before
                and not _block_entry_pause
            ):
                cash_now = _wallet_cash()
                if cash_now is not None and cash_now < unit:
                    _append_event(
                        {
                            "event": "skip_entry_low_cash",
                            "timestamp_hkt": now_hkt(),
                            "cash": cash_now,
                            "unit": unit,
                            "slug": market.slug,
                        }
                    )
                else:
                    leg1 = ev["would_leg1"]
                    side1 = str(leg1["side"])
                    intent_dict = leg1.get("fok_intent") or {}
                    intent = FokOrderIntent(
                        **{
                            k: intent_dict[k]
                            for k in intent_dict
                            if k in FokOrderIntent.__dataclass_fields__
                        }
                    )
                    intent.stake_usd = unit
                    res1 = _post_intent(intent)
                    if res1.get("posted"):
                        posted_live_orders += 1
                        row = {
                            "strategy": "DUAL_LEG_5M_LIVE_V1",
                            "mode": "LIVE",
                            "profile": profile_name,
                            "asset": "BTC",
                            "slug": market.slug,
                            "status": "one_leg",
                            "unit_usd": unit,
                            "opened_at_hkt": now_hkt(),
                            "first_leg": _leg_from_fill(
                                side1, intent, res1, snapshot["checkpoint"], t_rem
                            ),
                            "second_leg": None,
                            "legs_posted": 1,
                            "entry_regime": regime,
                            "reason": f"Live first leg {side1.upper()} FOK posted",
                        }
                        # Same-checkpoint second leg if V1 both-eligible hedge
                        if ev.get("would_hedge") and ev.get("would_leg2"):
                            leg2 = ev["would_leg2"]
                            side2 = str(leg2["side"])
                            intent2_dict = leg2.get("fok_intent") or {}
                            intent2 = FokOrderIntent(
                                **{
                                    k: intent2_dict[k]
                                    for k in intent2_dict
                                    if k in FokOrderIntent.__dataclass_fields__
                                }
                            )
                            intent2.stake_usd = unit
                            res2 = _post_intent(intent2)
                            if res2.get("posted"):
                                posted_live_orders += 1
                                row["second_leg"] = _leg_from_fill(
                                    side2, intent2, res2, snapshot["checkpoint"], t_rem
                                )
                                row["status"] = "hedged"
                                row["hedged_at_hkt"] = now_hkt()
                                row["legs_posted"] = 2
                                row["reason"] = "Both legs FOK filled same checkpoint"
                            else:
                                row["hedge_attempt_error"] = res2.get("error")
                                row["reason"] = (
                                    f"Leg1 filled; same-cp hedge failed: {res2.get('error')}"
                                )
                        state[market.slug] = row
                        _write_json(STATE_PATH, state)
                        _append_event(
                            {
                                "event": "entry",
                                "timestamp_hkt": now_hkt(),
                                "slug": market.slug,
                                "status": row["status"],
                                "legs_posted": row["legs_posted"],
                            }
                        )
                        print(
                            f"[live] ENTRY {market.slug} status={row['status']} "
                            f"posted={row['legs_posted']}",
                            flush=True,
                        )
                    else:
                        _append_event(
                            {
                                "event": "entry_failed",
                                "timestamp_hkt": now_hkt(),
                                "slug": market.slug,
                                "error": res1.get("error"),
                            }
                        )

            # --- Hedge delayed second leg (V1 paper behavior) ---
            elif row and row.get("status") == "one_leg":
                first_side = str((row.get("first_leg") or {}).get("side") or "")
                opposite = "down" if first_side == "up" else "up"
                book = up_book if opposite == "up" else down_book
                ask = book.get("best_ask")
                depth = float(book.get("ask_depth_usd") or 0)
                if (
                    t_rem >= profile.second_t_min
                    and ask is not None
                    and float(ask) <= profile.max_ask
                    and depth >= profile.min_depth_usd
                ):
                    cash_now = _wallet_cash()
                    if cash_now is not None and cash_now < unit:
                        _append_event(
                            {
                                "event": "skip_hedge_low_cash",
                                "timestamp_hkt": now_hkt(),
                                "cash": cash_now,
                                "slug": market.slug,
                            }
                        )
                    else:
                        intent2 = build_fok_buy_intent(
                            token_id=tokens[opposite],
                            side=opposite,
                            decision_ask=float(ask),
                            stake_usd=unit,
                            max_fill_price=profile.max_ask,
                            profile=profile_name,
                            slug=market.slug,
                        )
                        res2 = _post_intent(intent2)
                        if res2.get("posted"):
                            posted_live_orders += 1
                            row["second_leg"] = _leg_from_fill(
                                opposite, intent2, res2, snapshot["checkpoint"], t_rem
                            )
                            row["status"] = "hedged"
                            row["hedged_at_hkt"] = now_hkt()
                            row["legs_posted"] = int(row.get("legs_posted") or 1) + 1
                            row["reason"] = (
                                f"Second leg {opposite.upper()} FOK @ {float(ask):.2f}"
                            )
                            state[market.slug] = row
                            _write_json(STATE_PATH, state)
                            _append_event(
                                {
                                    "event": "hedge",
                                    "timestamp_hkt": now_hkt(),
                                    "slug": market.slug,
                                }
                            )
                            print(
                                f"[live] HEDGE {market.slug} {opposite} @ {ask}",
                                flush=True,
                            )
                        else:
                            _append_event(
                                {
                                    "event": "hedge_failed",
                                    "timestamp_hkt": now_hkt(),
                                    "slug": market.slug,
                                    "error": res2.get("error"),
                                }
                            )

            # Settle resolved markets so Live PnL / log stop showing 未結算 forever
            trades, settled_n, realized_pnl = settle_pending_live_trades(trades)
            if settled_n:
                _write_json(TRADES_PATH, trades)

            if samples % 1 == 0:
                cash_disp = _wallet_cash()
                status = {
                    "updated_at_hkt": now_hkt(),
                    "dry_run": False,
                    "DUAL_LEG_LIVE_DRY_RUN": "0",
                    "live_enabled": True,
                    "profile": profile_name,
                    "logic": ("V2" if profile_name == "v2" else "OLD_V1"),
                    "session_id": os.getenv("DUAL_LEG_LIVE_SESSION_ID") or None,
                    "unit_usd": unit,
                    "pair_lock_usd": round(unit * 2, 2),
                    "max_concurrent_pairs_gate": False,
                    "asset": "BTC",
                    "posted_live_orders": posted_live_orders,
                    "open_positions": len(state),
                    "open_slugs": list(state.keys()),
                    "samples_total": samples,
                    "errors": errors,
                    "wallet_cash_usdc": cash_disp,
                    "realized_pnl_usd": realized_pnl,
                    "settled_trades": sum(1 for t in trades if t.get("pnl") is not None),
                    "pending_settle_trades": sum(
                        1
                        for t in trades
                        if t.get("pnl") is None
                        and str(t.get("close_reason") or "") == "window_rolled_pending_outcome"
                    ),
                    "started_at_hkt": started,
                    "poll_sec": poll_sec,
                    "profile_params": profile_to_dict(profile),
                    "entry_pause": entry_pause_view(),
                    "last_eval": {
                        "slug": ev.get("slug"),
                        "checkpoint": ev.get("checkpoint"),
                        "would_enter_leg1": ev.get("would_enter_leg1"),
                        "would_hedge": ev.get("would_hedge"),
                        "skip_reasons": (ev.get("skip_reasons") or [])[:8],
                        "regime": (ev.get("regime")),
                    },
                    "note": (
                        f"BTC 雙腿 LIVE ON · {profile_name.upper()} · UNIT${unit:g} · 官方 outcome 後先計 Live PnL"
                    ),
                }
                _write_json(STATUS_PATH, status)

        except Exception as exc:
            errors += 1
            print(f"[live] loop error: {exc}", flush=True)
            _append_event(
                {
                    "event": "loop_error",
                    "timestamp_hkt": now_hkt(),
                    "error": str(exc),
                }
            )
            time.sleep(poll_sec)
            continue

        time.sleep(poll_sec)

    _append_event({"event": "live_daemon_stop", "timestamp_hkt": now_hkt()})
    print("[live] stopped cleanly", flush=True)
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="BTC dual-leg LIVE V1 daemon")
    parser.add_argument("--poll-sec", type=float, default=8.0)
    args = parser.parse_args()
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    return run_forever(max(2.0, float(args.poll_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
