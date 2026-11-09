#!/usr/bin/env python3
"""Family Son BTC 5m PAPER runner — isolated $100 wallet, never live.

Reads EnvironmentFile son-*.env (SON_FAMILY=newnew|a). Writes ONLY:
  logs/sons/{id}_trades.json, {id}_status.json, {id}_state.json

Stake sizing: V1 wallet-tier ladder at SON_INITIAL_CAPITAL_USD
(same evaluate_package2 sizing_wallet_usd as parent), then floor to
POLYMARKET_MIN_ORDER_SHARES (5) × ask. Do NOT also × capital/1000 —
that double-scaled vs the tier table (×0.12 at $100).
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path("/root/btc-5m-bot").resolve()
sys.path[:] = [str(ROOT)] + [
    p for p in sys.path if p and "weather-bot" not in p and Path(p).resolve() != ROOT
]


def _live_exec_mode() -> bool:
    return os.environ.get("SONU_LIVE_EXEC", "").strip().lower() in ("1", "true", "yes", "on")


# HARD paper-only before any trading imports (live-exec sets SONU_LIVE_EXEC=1 first)
if not _live_exec_mode():
    os.environ["TRADING_MODE"] = "paper"
    os.environ["DUAL_LEG_LIVE_DRY_RUN"] = "1"
    os.environ["REAL_MONEY"] = "0"

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from package2_strategy import (  # noqa: E402
    evaluate_package2,
    in_us_regular_session,
    package2_config,
)
from paper_fill_sim import simulate_live_style_fok  # noqa: E402
from polymarket_btc import (  # noqa: E402
    discover_current_market,
    fetch_order_book,
    seconds_to_end,
    summarize_book,
)
from polymarket_fees import estimate_taker_fee  # noqa: E402
from polymarket_window import fetch_market_outcome  # noqa: E402
from price_feeds import fetch_btc_usd  # noqa: E402
from strategy_rules import evaluate_snapshot  # noqa: E402

HKT = timezone(timedelta(hours=8))
CHECKPOINTS = [75, 60, 50, 40, 30, 25, 12, 5]
MIN_SHARES = float(os.getenv("POLYMARKET_MIN_ORDER_SHARES", "5"))
_STOP = False


def now_hkt() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def _handle(signum, _frame) -> None:  # noqa: ANN001
    global _STOP
    _STOP = True
    print(f"[son5m] signal {signum} — stopping @ {now_hkt()}", flush=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _parse_bands(raw: str | None) -> list[tuple[float, float]]:
    if not raw or not str(raw).strip():
        return []
    try:
        val = ast.literal_eval(str(raw).strip())
    except Exception:
        return []
    out: list[tuple[float, float]] = []
    for band in val or []:
        if isinstance(band, (list, tuple)) and len(band) >= 2:
            lo, hi = float(band[0]), float(band[1])
            if hi > lo:
                out.append((lo, hi))
    return out


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def _parse_skip_hours_hkt(raw: str | None) -> set[int]:
    """Son-only: skip NEW entries during these HKT hours (0-23). Empty = no hour gate."""
    if not raw or not str(raw).strip():
        return set()
    out: set[int] = set()
    for part in str(raw).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part and part.count("-") == 1:
            a, b = part.split("-", 1)
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            # half-open [lo, hi) if hi>lo; else treat as inclusive list
            if hi > lo:
                for h in range(lo, hi):
                    if 0 <= h <= 23:
                        out.add(h)
            else:
                if 0 <= lo <= 23:
                    out.add(lo)
                if 0 <= hi <= 23:
                    out.add(hi)
        else:
            try:
                h = int(part)
            except ValueError:
                continue
            if 0 <= h <= 23:
                out.add(h)
    return out


def _in_son_skip_hours_hkt(skip_hours: set[int]) -> bool:
    if not skip_hours:
        return False
    return datetime.now(HKT).hour in skip_hours



def refuse_unsafe_live() -> None:
    if os.getenv("REAL_MONEY", "0").strip() not in {"1", "true", "TRUE", "yes", "on"}:
        raise RuntimeError("REFUSING live: REAL_MONEY must be 1")
    if os.getenv("TRADING_MODE", "paper").strip().lower() != "live":
        raise RuntimeError("REFUSING live: TRADING_MODE must be live")
    if os.getenv("DRY_RUN", "1").strip().lower() not in {"0", "false", "no", "off"}:
        raise RuntimeError("REFUSING live: DRY_RUN must be 0")
    if os.getenv("DUAL_LEG_LIVE_DRY_RUN", "1").strip() not in {"0", "false", "no", "off"}:
        raise RuntimeError("REFUSING live: DUAL_LEG_LIVE_DRY_RUN must be 0")


def refuse_unsafe() -> None:
    if _live_exec_mode():
        refuse_unsafe_live()
        return
    if os.getenv("REAL_MONEY", "0").strip() in {"1", "true", "TRUE", "yes"}:
        raise RuntimeError("REFUSING: REAL_MONEY=1")
    if os.getenv("TRADING_MODE", "paper").strip().lower() != "paper":
        raise RuntimeError("REFUSING: TRADING_MODE must be paper")
    if os.getenv("DUAL_LEG_LIVE_DRY_RUN", "1").strip() != "1":
        raise RuntimeError("REFUSING: DUAL_LEG_LIVE_DRY_RUN must be 1")


def _live_wallet_cash() -> float:
    from scripts.dual_leg_live_daemon import _wallet_cash  # type: ignore

    cash = _wallet_cash()
    if cash is None:
        raise RuntimeError("REFUSING live: cannot read wallet cash")
    if float(cash) < 100.0:
        raise RuntimeError(f"REFUSING live: wallet ${float(cash):.2f} < $100")
    return float(cash)


def _execute_live_fok(
    *,
    market: Any,
    side: str,
    ask_f: float,
    stake: float,
    knobs: dict[str, Any],
    slug: str,
) -> dict[str, Any]:
    from dual_leg_live import build_fok_buy_intent, execute_fok_intent

    token_id = market.up_token_id if str(side).lower() == "up" else market.down_token_id
    max_fill = float(knobs.get("favorite_ask_max") or 0.88)
    intent = build_fok_buy_intent(
        token_id=str(token_id),
        side=str(side).lower(),
        decision_ask=ask_f,
        stake_usd=float(stake),
        max_fill_price=max_fill,
        profile="son_u_5m_single_leg",
        slug=slug,
        min_size=MIN_SHARES,
    )
    result = execute_fok_intent(intent, force_live=True)
    if result.get("status") != "live_filled":
        return {
            "filled": False,
            "no_fill_reason": result.get("error") or result.get("status") or "live_fok_failed",
            "live_result": result,
        }
    order = result.get("order") or {}
    fill_ask = float(
        order.get("live_ask_at_fill")
        or order.get("decision_ask")
        or order.get("price")
        or ask_f
    )
    fill_stake = float(order.get("stake_usd") or stake)
    shares = float(order.get("size") or order.get("size_shares") or (fill_stake / fill_ask if fill_ask else 0))
    return {
        "filled": True,
        "fill_price": fill_ask,
        "stake": fill_stake,
        "shares": shares,
        "execution_path": order.get("execution_path") or "FOK",
        "order_id": order.get("orderID") or order.get("order_id"),
        "live_result": result,
    }


def install_son_knobs(family: str) -> dict[str, Any]:
    """Monkeypatch package2_config so evaluate_package2 sees son ask/skip/offhours."""
    import package2_strategy as p2

    fav_min = float(os.getenv("FAVORITE_ASK_MIN") or 0.72)
    fav_max = float(os.getenv("FAVORITE_ASK_MAX") or 0.84)
    bands = _parse_bands(os.getenv("FAVORITE_SKIP_ASK_BANDS"))
    if _env_truthy("REMOVE_SKIP"):
        bands = []
    offhours = os.getenv("OFFHOURS_STAKE_MULT")
    hard_clamp = _env_truthy("HARD_CLAMP")
    hard_min = _env_truthy("HARD_MIN_ASK")
    only_8084 = _env_truthy("ONLY_BAND_8084")

    variant = "package3" if family == "newnew" else "package2"
    skip_hours = sorted(_parse_skip_hours_hkt(os.getenv("SON_SKIP_HOURS_HKT")))
    knobs = {
        "family": family,
        "variant": variant,
        "favorite_ask_min": fav_min,
        "favorite_ask_max": fav_max,
        "skip_bands": bands,
        "offhours_stake_mult": float(offhours) if offhours is not None else None,
        "hard_clamp": hard_clamp,
        "hard_min_ask": hard_min,
        "only_band_8084": only_8084,
        "skip_hours_hkt": skip_hours,
    }

    _orig = p2.package2_config

    def _patched(asset: str = "btc", *, variant: str | None = None) -> dict[str, Any]:
        # Force family baseline variant unless caller overrides oddly
        force = knobs["variant"] if variant is None else variant
        if family == "newnew":
            force = "package3"
        elif family == "a":
            force = "package2"
        cfg = _orig(asset, variant=force)
        cfg = dict(cfg)
        lo, hi = fav_min, fav_max
        if only_8084:
            lo, hi = 0.80, 0.84
            bands_use: list[tuple[float, float]] = []
            cfg["favorite_only"] = True
        else:
            bands_use = list(bands)
        cfg["favorite_ask"] = lo
        cfg["max_ask_favorite"] = hi
        cfg["favorite_skip_ask_bands"] = bands_use
        if hard_clamp or hard_min or only_8084:
            cfg["min_ask"] = lo
            cfg["favorite_only"] = True
            # Clamp all tiers into [lo, hi]
            cfg["max_ask"] = hi
            cfg["max_ask_underdog"] = hi
        if knobs["offhours_stake_mult"] is not None:
            cfg["offhours_stake_mult"] = float(knobs["offhours_stake_mult"])
        return cfg

    p2.package2_config = _patched  # type: ignore[assignment]
    return knobs


def scale_stake(parent_stake: float, ask: float, capital: float | None = None) -> float:
    """Floor wallet-tier stake to min-shares×ask (no extra capital/1000 scale).

    parent_stake is already sized by evaluate_package2(sizing_wallet_usd=son capital).
    The old capital/1000 factor double-counted vs the V1 tier table.
    Optional SON_FIXED_STAKE_USD forces ticket size (e.g. Son U live-prep $10).
    Optional SON_MAX_STAKE_USD caps ticket size (e.g. Fav Scalp ≤$8).
    """
    try:
        fixed = os.getenv("SON_FIXED_STAKE_USD")
        if fixed is not None and str(fixed).strip() != "":
            out = round(float(fixed), 2)
            floor = round(MIN_SHARES * float(ask), 2) if ask and ask > 0 else 0.0
            out = max(out, floor, 0.5)
            return round(float(out), 2)
    except (TypeError, ValueError):
        pass
    scaled = round(float(parent_stake), 2)
    floor = round(MIN_SHARES * float(ask), 2) if ask and ask > 0 else 0.0
    out = max(scaled, floor, 0.5)
    try:
        cap = os.getenv("SON_MAX_STAKE_USD")
        if cap is not None and str(cap).strip() != "":
            out = min(out, float(cap))
    except (TypeError, ValueError):
        pass
    return round(float(out), 2)


def _enrich_books(raw: dict) -> dict:
    out = summarize_book(raw) or {}
    asks = raw.get("asks") or raw.get("sells") or []
    bids = raw.get("bids") or raw.get("buys") or []
    if isinstance(asks, list) and asks and "asks" not in out:
        out["asks"] = asks
    if isinstance(bids, list) and bids and "bids" not in out:
        out["bids"] = bids
    return out


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
            print(f"[son5m] settle fetch fail {slug}: {exc}", flush=True)
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
            }
        )
        changed += 1
        print(f"[son5m] SETTLED {slug} {row['trade_result']} pnl=${row['pnl']}", flush=True)
        out.append(row)
    realized = 0.0
    for row in out:
        if row.get("entered") and row.get("pnl") is not None:
            try:
                realized += float(row["pnl"])
            except (TypeError, ValueError):
                pass
    return out, changed, round(realized, 2)


def run_forever(son_id: str, poll_sec: float) -> int:
    refuse_unsafe()
    live_mode = _live_exec_mode()
    if not _env_truthy("SON_LAUNCH"):
        print(f"[son5m] {son_id} SON_LAUNCH!=1 — refuse", flush=True)
        return 2

    family = (os.getenv("SON_FAMILY") or "").strip().lower()
    if family not in {"newnew", "a"}:
        print(f"[son5m] bad SON_FAMILY={family!r}", flush=True)
        return 2

    if live_mode:
        try:
            wallet_cap = _live_wallet_cash()
        except RuntimeError as exc:
            print(f"[son5m] {exc}", file=sys.stderr, flush=True)
            return 2
        capital = wallet_cap
        os.environ["SON_INITIAL_CAPITAL_USD"] = str(round(capital, 2))
    else:
        capital = float(os.getenv("SON_INITIAL_CAPITAL_USD") or 100)
    trade_mode_label = "LIVE" if live_mode else "PAPER"
    try:
        _ke = os.getenv("SON_KILL_EQUITY_USD")
        kill_equity = float(_ke) if _ke not in (None, "") else None
        if kill_equity is not None and kill_equity <= 0:
            kill_equity = None
    except (TypeError, ValueError):
        kill_equity = None
    killed = False
    display = os.getenv("SON_DISPLAY_NAME") or son_id
    session_id = os.getenv("SON_SESSION_ID") or (
        f"{'live' if live_mode else 'paper'}-{son_id}-{datetime.now(HKT).strftime('%Y%m%d-%H%M%S')}"
    )
    status_path = Path(os.environ.get("SON_STATUS_PATH") or ROOT / "logs" / "sons" / f"{son_id}_status.json")
    trades_path = Path(os.environ.get("SON_TRADE_LOG") or ROOT / "logs" / "sons" / f"{son_id}_trades.json")
    state_path = ROOT / "logs" / "sons" / f"{son_id}_state.json"

    knobs = install_son_knobs(family)
    variant = knobs["variant"]

    state = _read_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    trades = _read_json(trades_path, [])
    if not isinstance(trades, list):
        trades = []

    # Fresh session: keep historical trades but tag new session id going forward
    decided_slugs: set[str] = set(state.get("decided_slugs") or [])
    open_by_slug: dict[str, dict] = dict(state.get("open_by_slug") or {})
    current_slug = state.get("current_slug")
    open_price = state.get("open_btc_price")
    seen_cps: set[int] = set(state.get("seen_checkpoints") or [])

    started = now_hkt()
    samples = 0
    entries = 0
    skips = 0
    errors = 0

    print(
        f"[son5m] START {trade_mode_label} son={son_id} family={family} variant={variant} "
        f"capital=${capital:g} session={session_id} stake_sizing=wallet_tier+min_shares_floor "
        f"min_shares_floor={MIN_SHARES} knobs={knobs} @ {started}",
        flush=True,
    )

    while not _STOP:
        try:
            refuse_unsafe()
        except RuntimeError as exc:
            print(f"[son5m] {exc}", file=sys.stderr, flush=True)
            return 3

        try:
            market = discover_current_market()
            if not market:
                time.sleep(poll_sec)
                continue
            t_rem = float(seconds_to_end(market))

            if market.slug != current_slug:
                # finalize previous: log skip if never decided
                if current_slug and current_slug not in decided_slugs:
                    skip_row = {
                        "strategy": f"SON_{family.upper()}",
                        "session_id": session_id,
                        "mode": "PAPER",
                        "son_id": son_id,
                        "slug": current_slug,
                        "entered": False,
                        "trade_result": "SKIP",
                        "stake": 0.0,
                        "strategy_reason": "Skipped: window rolled without eligible entry",
                        "skip_category": "window_end_no_entry",
                        "logged_at_hkt": now_hkt(),
                        "paper": True,
                    }
                    trades.append(skip_row)
                    decided_slugs.add(current_slug)
                    skips += 1
                    _write_json(trades_path, trades)
                # settle any opens
                trades, settled_n, _ = settle_trades(trades)
                if settled_n:
                    _write_json(trades_path, trades)
                btc = fetch_btc_usd()
                open_price = float(btc["price"])
                current_slug = market.slug
                seen_cps = set()
                print(f"[son5m] window {market.slug} t_rem={t_rem:.1f}", flush=True)

            # settle periodically
            if samples % 8 == 0:
                trades, settled_n, _ = settle_trades(trades)
                if settled_n:
                    _write_json(trades_path, trades)

            for cp in CHECKPOINTS:
                if cp in seen_cps:
                    continue
                if not (cp - 3 <= t_rem <= cp + 2):
                    continue
                label = f"T-{cp}"
                btc = fetch_btc_usd()
                px = float(btc["price"])
                if open_price is None:
                    open_price = px
                delta_usd = px - float(open_price)
                delta_pct = (delta_usd / float(open_price) * 100) if open_price else 0.0
                up_raw = fetch_order_book(market.up_token_id)
                down_raw = fetch_order_book(market.down_token_id)
                up_book = _enrich_books(up_raw)
                down_book = _enrich_books(down_raw)
                ev = evaluate_snapshot(t_rem, delta_usd, delta_pct, up_book, down_book)
                snap = {
                    "checkpoint": label,
                    "t_remaining": round(t_rem, 1),
                    "btc_price": px,
                    "open_btc_price": open_price,
                    "delta_usd": round(delta_usd, 2),
                    "delta_pct": round(delta_pct, 4),
                    "up_book": up_book,
                    "down_book": down_book,
                    **ev,
                }
                seen_cps.add(cp)
                samples += 1

                if current_slug in decided_slugs:
                    continue

                side = str(snap.get("side") or "up")
                ask = snap.get("ask")
                timing_ok = bool(snap.get("in_entry_window")) and not (snap.get("skip_reasons") or [])

                if ask is None or float(ask) <= 0:
                    continue
                ask_f = float(ask)


                # Son-only evening / hour skip (HKT). Does not touch parents.
                skip_hours = set(knobs.get("skip_hours_hkt") or [])
                if skip_hours and _in_son_skip_hours_hkt(skip_hours):
                    if t_rem <= 22 and current_slug not in decided_slugs and cp <= 25:
                        hour_now = datetime.now(HKT).hour
                        row = {
                            "strategy": f"SON_{family.upper()}",
                            "session_id": session_id,
                            "mode": "PAPER",
                            "son_id": son_id,
                            "slug": current_slug,
                            "title": market.title,
                            "side": side,
                            "ask": ask_f,
                            "checkpoint": label,
                            "entered": False,
                            "trade_result": "SKIP",
                            "stake": 0.0,
                            "strategy_reason": (
                                f"Skipped: SON_SKIP_HOURS_HKT hour={hour_now} "
                                f"in {sorted(skip_hours)}"
                            ),
                            "skip_category": "evening_hkt_skip",
                            "logged_at_hkt": now_hkt(),
                            "paper": True,
                            "knobs": knobs,
                        }
                        trades.append(row)
                        decided_slugs.add(current_slug)
                        skips += 1
                        _write_json(trades_path, trades)
                    continue

                # Hard clamp / only-band gate before strategy
                lo = float(knobs["favorite_ask_min"])
                hi = float(knobs["favorite_ask_max"])
                if knobs["only_band_8084"]:
                    lo, hi = 0.80, 0.84
                if (knobs["hard_clamp"] or knobs["hard_min_ask"] or knobs["only_band_8084"]) and not (
                    lo - 1e-9 <= ask_f <= hi + 1e-9
                ):
                    # wait for better checkpoint unless late
                    if t_rem <= 22:
                        row = {
                            "strategy": f"SON_{family.upper()}",
                            "session_id": session_id,
                            "mode": "PAPER",
                            "son_id": son_id,
                            "slug": current_slug,
                            "title": market.title,
                            "side": side,
                            "ask": ask_f,
                            "checkpoint": label,
                            "entered": False,
                            "trade_result": "SKIP",
                            "stake": 0.0,
                            "strategy_reason": f"Skipped: ask {ask_f:.2f} outside hard band [{lo:.2f},{hi:.2f}]",
                            "skip_category": "hard_ask_clamp",
                            "logged_at_hkt": now_hkt(),
                            "paper": True,
                            "knobs": knobs,
                        }
                        trades.append(row)
                        decided_slugs.add(current_slug)
                        skips += 1
                        _write_json(trades_path, trades)
                    continue

                if not timing_ok:
                    # only finalize skip near end of window
                    if t_rem <= 22 and current_slug not in decided_slugs and cp <= 25:
                        row = {
                            "strategy": f"SON_{family.upper()}",
                            "session_id": session_id,
                            "mode": "PAPER",
                            "son_id": son_id,
                            "slug": current_slug,
                            "title": market.title,
                            "side": side,
                            "ask": ask_f,
                            "checkpoint": label,
                            "entered": False,
                            "trade_result": "SKIP",
                            "stake": 0.0,
                            "strategy_reason": f"Skipped: timing/filter — {', '.join(snap.get('skip_reasons') or [])}",
                            "skip_category": "timing_filter",
                            "logged_at_hkt": now_hkt(),
                            "paper": True,
                        }
                        trades.append(row)
                        decided_slugs.add(current_slug)
                        skips += 1
                        _write_json(trades_path, trades)
                    continue

                # Bankroll gate
                realized = sum(
                    float(r["pnl"])
                    for r in trades
                    if r.get("entered") and r.get("pnl") is not None
                )
                open_stake = sum(float(r.get("stake") or 0) for r in open_by_slug.values())
                bankroll = max(0.0, capital + realized - open_stake)
                equity_now = capital + realized
                if kill_equity is not None and equity_now <= float(kill_equity) + 1e-9:
                    killed = True

                tier = evaluate_package2(
                    ask_f,
                    snapshot=snap,
                    variant=variant,
                    sizing_wallet_usd=capital,
                )
                if not tier.get("entered"):
                    # keep waiting for a later checkpoint that might enter
                    if t_rem <= 22:
                        row = {
                            "strategy": f"SON_{family.upper()}",
                            "session_id": session_id,
                            "mode": "PAPER",
                            "son_id": son_id,
                            "slug": current_slug,
                            "title": market.title,
                            "side": side,
                            "ask": ask_f,
                            "checkpoint": label,
                            "entered": False,
                            "trade_result": "SKIP",
                            "stake": 0.0,
                            "strategy_reason": tier.get("strategy_reason") or "Skipped",
                            "skip_category": tier.get("skip_category") or "strategy",
                            "logged_at_hkt": now_hkt(),
                            "paper": True,
                            "knobs": knobs,
                        }
                        trades.append(row)
                        decided_slugs.add(current_slug)
                        skips += 1
                        _write_json(trades_path, trades)
                    continue

                parent_stake = float(tier.get("stake") or 0)
                if killed:
                    continue
                stake = scale_stake(parent_stake, ask_f, capital)
                if stake > bankroll + 1e-9:
                    row = {
                        "strategy": f"SON_{family.upper()}",
                        "session_id": session_id,
                        "mode": "PAPER",
                        "son_id": son_id,
                        "slug": current_slug,
                        "title": market.title,
                        "side": side,
                        "ask": ask_f,
                        "checkpoint": label,
                        "entered": False,
                        "trade_result": "SKIP",
                        "stake": 0.0,
                        "strategy_reason": (
                            f"Skipped: insufficient son bankroll ${bankroll:.2f} < ${stake:.2f}"
                        ),
                        "skip_category": "insufficient_bankroll",
                        "logged_at_hkt": now_hkt(),
                        "paper": True,
                    }
                    trades.append(row)
                    decided_slugs.add(current_slug)
                    skips += 1
                    _write_json(trades_path, trades)
                    continue

                if live_mode:
                    fill = _execute_live_fok(
                        market=market,
                        side=side,
                        ask_f=ask_f,
                        stake=stake,
                        knobs=knobs,
                        slug=current_slug,
                    )
                else:
                    fill = simulate_live_style_fok(
                        side=side,
                        decision_ask=ask_f,
                        intended_stake=stake,
                        tier_name=str(tier.get("tier") or "favorite"),
                        up_book=up_book,
                        down_book=down_book,
                        min_shares=MIN_SHARES,
                        max_ask_cap=float(knobs["favorite_ask_max"])
                        if (knobs["hard_clamp"] or knobs["only_band_8084"])
                        else None,
                    )
                if not fill.get("filled"):
                    # try later checkpoint
                    if t_rem <= 22:
                        row = {
                            "strategy": f"SON_{family.upper()}",
                            "session_id": session_id,
                            "mode": "PAPER",
                            "son_id": son_id,
                            "slug": current_slug,
                            "title": market.title,
                            "side": side,
                            "ask": ask_f,
                            "checkpoint": label,
                            "entered": False,
                            "trade_result": "NO_FILL",
                            "stake": 0.0,
                            "strategy_reason": f"Paper FOK no fill: {fill.get('no_fill_reason')}",
                            "skip_category": "paper_fok_no_fill",
                            "fill_meta": fill,
                            "logged_at_hkt": now_hkt(),
                            "paper": True,
                        }
                        trades.append(row)
                        decided_slugs.add(current_slug)
                        skips += 1
                        _write_json(trades_path, trades)
                    continue

                fill_ask = float(fill["fill_price"])
                fill_stake = float(fill["stake"])
                shares = float(fill["shares"])
                fee = estimate_taker_fee(shares, fill_ask)
                row = {
                    "strategy": f"SON_{family.upper()}",
                    "session_id": session_id,
                    "mode": trade_mode_label,
                    "son_id": son_id,
                    "display_name": display,
                    "slug": current_slug,
                    "title": market.title,
                    "side": side,
                    "ask": ask_f,
                    "entry_ask": fill_ask,
                    "decision_ask": ask_f,
                    "checkpoint": label,
                    "t_remaining": round(t_rem, 1),
                    "entered": True,
                    "filled": True,
                    "trade_result": "OPEN",
                    "tier": tier.get("tier"),
                    "stake": round(fill_stake, 2),
                    "parent_stake_before_scale": parent_stake,
                    "stake_scale": 1.0,
                    "stake_sizing_method": "wallet_tier_at_son_capital+min_shares_floor",
                    "shares": round(shares, 6),
                    "estimated_taker_fee": round(fee, 4),
                    "strategy_reason": (
                        f"{tier.get('strategy_reason')} · son wallet-tier "
                        f"→ ${fill_stake:.2f} (min-shares floor ok)"
                    ),
                    "execution_path": fill.get("execution_path")
                    if live_mode
                    else "PAPER_FOK_SIM",
                    "paper_fill_model": None if live_mode else "live_style_fok",
                    "fill_meta": {
                        k: fill.get(k)
                        for k in (
                            "fill_slippage",
                            "max_fill_price",
                            "live_ask_at_fill",
                            "book_levels_used",
                            "order_id",
                        )
                        if fill.get(k) is not None
                    },
                    "opened_at_hkt": now_hkt(),
                    "logged_at_hkt": now_hkt(),
                    "paper": not live_mode,
                    "dry_run": not live_mode,
                    "posted_live_order": bool(live_mode),
                    "knobs": knobs,
                    "initial_capital_usd": capital,
                }
                trades.append(row)
                open_by_slug[current_slug] = row
                decided_slugs.add(current_slug)
                entries += 1
                _write_json(trades_path, trades)
                print(
                    f"[son5m] ENTRY {current_slug} {side} @ {fill_ask:.3f} "
                    f"stake=${fill_stake:.2f} cp={label}",
                    flush=True,
                )

            # status heartbeat
            trades2, _, realized_pnl = settle_trades(trades)
            if trades2 is not trades:
                trades = trades2
            # drop settled from open map
            for slug, orow in list(open_by_slug.items()):
                match = next(
                    (
                        t
                        for t in trades
                        if t.get("slug") == slug and t.get("entered") and t.get("session_id") == session_id
                    ),
                    None,
                )
                if match and match.get("pnl") is not None:
                    open_by_slug.pop(slug, None)

            trade_count = sum(
                1
                for t in trades
                if t.get("entered") and t.get("session_id") == session_id
            )
            status = {
                "id": son_id,
                "display_name": display,
                "armed": True,
                "launched": True,
                "status": f"RUNNING · {'live' if live_mode else 'paper'}",
                "initial_capital_usd": capital,
                "capital_usd": capital,
                "pnl": realized_pnl,
                "killed": bool(killed),
                "kill_equity_usd": kill_equity,
                "pnl_usd": realized_pnl,
                "trade_count": trade_count,
                "entries": entries,
                "skips": skips,
                "samples": samples,
                "errors": errors,
                "session_id": session_id,
                "family": family,
                "kind": "5m",
                "variant": variant,
                "knobs": knobs,
                "stake_sizing": {
                    "method": "evaluate_package2 sizing_wallet_usd=son capital + min_shares*ask floor",
                    "capital": capital,
                    "parent_bankroll_ref_note": "parent sessions still start at $1000; son uses V1 wallet-tier at $100 (x0.12), not linear capital/1000",
                    "scale": 1.0,
                    "min_shares": MIN_SHARES,
                },
                "heartbeat_at_hkt": now_hkt(),
                "started_at_hkt": started,
                "current_slug": current_slug,
                "open_positions": len(open_by_slug),
                "dry_run": not live_mode,
                "TRADING_MODE": "live" if live_mode else "paper",
                "DUAL_LEG_LIVE_DRY_RUN": "0" if live_mode else "1",
                "REAL_MONEY": "1" if live_mode else "0",
                "posted_live_orders": entries if live_mode else 0,
                "config_path": str(ROOT / "configs" / "sons" / f"{son_id}.env"),
                "trades_path": str(trades_path),
                "note": (
                    "Son U 5m LIVE FOK · single-leg · wallet capital"
                    if live_mode
                    else "Son 5m paper runner · isolated $100 · never posts CLOB"
                ),
            }
            _write_json(status_path, status)
            _write_json(
                state_path,
                {
                    "current_slug": current_slug,
                    "open_btc_price": open_price,
                    "seen_checkpoints": list(seen_cps),
                    "decided_slugs": list(decided_slugs)[-200:],
                    "open_by_slug": open_by_slug,
                    "session_id": session_id,
                },
            )

        except Exception as exc:
            errors += 1
            print(f"[son5m] loop error: {exc}", flush=True)

        time.sleep(poll_sec)

    print(f"[son5m] stopped @ {now_hkt()}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Family Son 5m PAPER daemon")
    ap.add_argument("--son-id", required=True)
    ap.add_argument("--poll-sec", type=float, default=4.0)
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    return run_forever(args.son_id, max(2.0, float(args.poll_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
