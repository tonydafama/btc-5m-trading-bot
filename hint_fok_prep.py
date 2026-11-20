"""Default-off prep path for a later $1 BTC 5m trial.

One Polymarket BTC 5-minute FOK buy per window, direction taken from the
glance-board hint. Nothing here is armed.

This module does not read the global DRY_RUN, REAL_MONEY, or LIVE_ARMED
flags, and it does not touch Mid-band Range80 or any other bot. There is
no systemd unit. The dashboard must only call public_prep_status().

All four of these must be the exact values below before a send is even
attempted. Anything else, including an unset variable, stays shut:

    HINT_FOK_ENABLED=1
    HINT_FOK_DRY_RUN=0
    HINT_FOK_ARMED=1
    HINT_FOK_REAL_MONEY=1

Do not set those until the user says Go Live in the finance chat.
A $1 request is not what the exchange usually accepts: the minimum size
is about 5 shares, so at common asks of 0.80–0.85 the order is about
$4.00–$4.25.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

INTENDED_USD = 1.0
MIN_SHARES = 5.0
TICK = 0.01
EXAMPLE_ASKS = (0.80, 0.85)

_ADDRESS = re.compile(r"0x[a-fA-F0-9]{8,}")


def _exact(name: str) -> str:
    return os.getenv(name, "").strip()


def gates_open() -> bool:
    """True only when every dedicated flag is explicitly in the send position."""
    return (
        _exact("HINT_FOK_ENABLED") == "1"
        and _exact("HINT_FOK_DRY_RUN") == "0"
        and _exact("HINT_FOK_ARMED") == "1"
        and _exact("HINT_FOK_REAL_MONEY") == "1"
    )


def _round_tick(price: float, tick: float = TICK) -> float:
    rounded = round(round(price / tick) * tick, 4)
    return max(tick, min(1.0 - tick, rounded))


def size_fok(ask: float, *, intended_usd: float = INTENDED_USD, min_shares: float = MIN_SHARES) -> dict[str, Any]:
    """Size one FOK buy.

    The existing CLOB helper spends `stake` in USD and then bumps the share
    count up to min_shares. Passing the raw $1 would not meet a 5-share
    floor at typical asks, so the stake sent later is the floored notional.
    """
    raw_ask = float(ask)
    if not 0 < raw_ask < 1:
        raise ValueError("ask out of range")
    limit_price = _round_tick(raw_ask)
    raw_shares = intended_usd / limit_price
    shares = max(min_shares, raw_shares)
    notional = round(shares * limit_price, 2)
    guard = 0
    while round(notional / limit_price, 2) + 1e-9 < min_shares and guard < 50:
        notional = round(notional + 0.01, 2)
        guard += 1
    shares_sent = round(notional / limit_price, 2)
    return {
        "intended_usd": round(float(intended_usd), 2),
        "ask": raw_ask,
        "limit_price": limit_price,
        "min_shares": float(min_shares),
        "raw_shares": raw_shares,
        "shares": shares_sent,
        "notional_usd": notional,
        "floored_to_min_shares": raw_shares + 1e-9 < min_shares,
    }


def floor_examples() -> list[dict[str, Any]]:
    rows = []
    for ask in EXAMPLE_ASKS:
        sized = size_fok(ask)
        rows.append({"ask": ask, "shares": sized["shares"], "notional_usd": sized["notional_usd"]})
    return rows


def public_prep_status() -> dict[str, Any]:
    """Read-only status for the glance board. Never places an order."""
    enabled = _exact("HINT_FOK_ENABLED") == "1"
    dry_run = _exact("HINT_FOK_DRY_RUN") != "0"
    armed = _exact("HINT_FOK_ARMED") == "1"
    real_money = _exact("HINT_FOK_REAL_MONEY") == "1"
    return {
        "enabled": enabled,
        "dry_run": dry_run,
        "armed": armed,
        "real_money": real_money,
        "will_send": gates_open(),
        "intended_usd": INTENDED_USD,
        "min_shares": MIN_SHARES,
        "floor_examples": floor_examples(),
        "max_orders_per_window": 1,
        "order_type": "FOK",
        "market": "btc-updown-5m",
        "separate_from": "midband-range80",
        "systemd_unit": None,
        "page_can_send": False,
    }


def plan_fok(
    *,
    direction: str,
    ask: float,
    token_id: str,
    window_start: int,
    slug: str,
) -> dict[str, Any]:
    if direction not in ("up", "down"):
        raise ValueError("direction must be up or down")
    if not str(token_id).strip():
        raise ValueError("token missing")
    sized = size_fok(ask)
    return {
        "market": "btc-updown-5m",
        "order_type": "FOK",
        "side": "BUY",
        "outcome": direction,
        "token_id": str(token_id),
        "window_start": int(window_start),
        "slug": str(slug),
        "max_orders_this_window": 1,
        **sized,
    }


def _ledger_path(path: Path | None) -> Path:
    if path is not None:
        return path
    override = os.getenv("HINT_FOK_LEDGER_PATH", "").strip()
    if override:
        return Path(override)
    root = os.getenv("BTC_5M_ROOT", "").strip()
    base = Path(root) if root else Path(__file__).resolve().parent
    return base / "logs" / "hint_fok_prep_ledger.json"


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return _ADDRESS.sub("[redacted]", value)
    if isinstance(value, dict):
        return {str(key): _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _public_error(exc: BaseException) -> str:
    return _scrub(str(exc))[:240]


def _claim_window(path: Path, window_start: int, record: dict[str, Any]) -> bool:
    """Return False if this window was already attempted. Claim before sending."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data: dict[str, Any] = {"windows": {}}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("windows"), dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {"windows": {}}
        windows = data.setdefault("windows", {})
        key = str(int(window_start))
        if key in windows:
            return False
        windows[key] = record
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True


def maybe_place_fok(
    plan: dict[str, Any],
    *,
    place_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ledger_path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Place at most one FOK buy. Closed gates never call place_fn."""
    if not gates_open():
        return {
            "ok": True,
            "placed": False,
            "blocked": True,
            "attempted": False,
            "message": "預備路徑關閉，未呼叫落單。",
        }
    path = _ledger_path(ledger_path)
    stamp = time.time() if now is None else float(now)
    record = {
        "at": stamp,
        "outcome": plan.get("outcome"),
        "slug": plan.get("slug"),
        "notional_usd": plan.get("notional_usd"),
        "shares": plan.get("shares"),
        "attempted": True,
    }
    if not _claim_window(path, int(plan["window_start"]), record):
        return {
            "ok": True,
            "placed": False,
            "blocked": True,
            "attempted": False,
            "message": "呢個 5 分鐘窗已經試過一張單，唔會再落。",
        }
    sender = place_fn or _default_place
    try:
        response = sender(plan)
    except Exception as exc:
        return {
            "ok": False,
            "placed": False,
            "blocked": False,
            "attempted": True,
            "message": _public_error(exc),
        }
    return {
        "ok": True,
        "placed": True,
        "blocked": False,
        "attempted": True,
        "message": "已送出一張 FOK。",
        "notional_usd": plan.get("notional_usd"),
        "shares": plan.get("shares"),
        "outcome": plan.get("outcome"),
        "response": _scrub(response) if isinstance(response, dict) else {"response": _scrub(str(response))[:200]},
    }


def _default_place(plan: dict[str, Any]) -> dict[str, Any]:
    """One FOK buy. Not used unless every gate is already open."""
    from btc_clob import place_buy_order

    return place_buy_order(
        token_id=str(plan["token_id"]),
        price=float(plan["limit_price"]),
        stake_usd=float(plan["notional_usd"]),
        tick_size="0.01",
        neg_risk=False,
        min_size=float(plan["min_shares"]),
        use_limit=False,
    )


def default_quote(hint: dict[str, Any]) -> dict[str, Any]:
    """Resolve the current BTC 5m outcome token. Only called when gates are open."""
    from btc_clob import fetch_live_best_ask
    from polymarket_crypto import discover_current_market

    market = discover_current_market("btc")
    if market is None:
        raise RuntimeError("搵唔到而家嘅 BTC 5 分鐘窗")
    direction = hint.get("direction")
    if direction not in ("up", "down"):
        raise RuntimeError("提示冇升跌方向")
    token = market.up_token_id if direction == "up" else market.down_token_id
    ask = fetch_live_best_ask(token)
    if ask is None:
        raise RuntimeError("冇賣價，唔落單")
    return {
        "token_id": token,
        "ask": float(ask),
        "slug": market.slug,
        "window_start": int(market.window_start_ts),
    }


def run_prep(
    *,
    hint_fn: Callable[[], dict[str, Any]] | None = None,
    quote_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    place_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ledger_path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Entry point used by the manual script. Shut gates return before any quote or send."""
    prep = public_prep_status()
    if not prep["will_send"]:
        return {
            "ok": True,
            "placed": False,
            "blocked": True,
            "message": "預備路徑關閉：未啟用、DRY_RUN 開、未武裝、真錢旗關。唔會落單。",
            "prep": prep,
        }
    if hint_fn is None:
        from predict_hint import build_hint_payload

        hint_fn = build_hint_payload
    hint = hint_fn()
    if not hint.get("ok") or hint.get("direction") not in ("up", "down"):
        return {
            "ok": False,
            "placed": False,
            "blocked": True,
            "message": "冇升跌提示，唔落單。",
            "prep": prep,
        }
    quote = (quote_fn or default_quote)(hint)
    plan = plan_fok(
        direction=str(hint["direction"]),
        ask=float(quote["ask"]),
        token_id=str(quote["token_id"]),
        window_start=int(quote["window_start"]),
        slug=str(quote.get("slug") or ""),
    )
    result = maybe_place_fok(plan, place_fn=place_fn, ledger_path=ledger_path, now=now)
    result["prep"] = prep
    result["plan"] = {
        "outcome": plan["outcome"],
        "slug": plan["slug"],
        "window_start": plan["window_start"],
        "notional_usd": plan["notional_usd"],
        "shares": plan["shares"],
        "intended_usd": plan["intended_usd"],
        "floored_to_min_shares": plan["floored_to_min_shares"],
    }
    return result
