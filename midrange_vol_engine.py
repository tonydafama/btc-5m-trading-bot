"""Session, events, settlement, and live posting for midrange-vol strategy."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from midrange_vol_strategy import STAKE_USD, STRATEGY_ID, STRATEGY_LABEL, rules_summary
from new_strategy import calc_loss_pnl, calc_win_pnl, now_hkt_str, side_to_contract, trade_result
from polymarket_btc import fetch_market_outcome

HKT = ZoneInfo("Asia/Hong_Kong")


def repo_root() -> Path:
    return Path(os.getenv("BTC_5M_ROOT", Path(__file__).resolve().parent))


def log_dir() -> Path:
    p = repo_root() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def session_path() -> Path:
    return log_dir() / "midrange_vol_session.json"


def trades_path() -> Path:
    return log_dir() / "midrange_vol_trades.json"


def events_path() -> Path:
    return log_dir() / "midrange_vol_events.jsonl"


def state_path() -> Path:
    return log_dir() / "midrange_vol_daemon_state.json"


def exec_env_path() -> Path:
    return repo_root() / "config" / "midrange_vol_live.env"


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def trading_mode() -> str:
    return (os.getenv("MIDVOL_TRADING_MODE") or os.getenv("TRADING_MODE") or "paper").lower()


def live_armed() -> bool:
    raw = os.getenv("MIDVOL_LIVE_ARMED", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def dry_run() -> bool:
    if not live_armed():
        return True
    return (os.getenv("MIDVOL_DRY_RUN", "0") or "0").strip().lower() in ("1", "true", "yes")


def new_session_id() -> str:
    return datetime.now(HKT).strftime("midvol-%Y%m%d-%H%M%S")


def ensure_session(*, reset: bool = False) -> dict[str, Any]:
    if reset or not session_path().is_file():
        sess = {
            "session_id": new_session_id(),
            "strategy": STRATEGY_ID,
            "strategy_label": STRATEGY_LABEL,
            "created_at_hkt": now_hkt_str(),
            "validated": False,
            "rules": rules_summary(),
            "trading_mode": trading_mode(),
            "live_armed": live_armed(),
        }
        _save_json(session_path(), sess)
        _save_json(trades_path(), [])
        return sess
    path = session_path()
    sess = _load_json(path, {})
    if not sess.get("session_id"):
        return ensure_session(reset=True)
    return sess


def load_trades() -> list[dict[str, Any]]:
    rows = _load_json(trades_path(), [])
    return rows if isinstance(rows, list) else []


def save_trades(rows: list[dict[str, Any]]) -> None:
    _save_json(trades_path(), rows[-500:])


def append_event(row: dict[str, Any]) -> None:
    row.setdefault("timestamp_hkt", now_hkt_str())
    row.setdefault("session_id", ensure_session().get("session_id"))
    with events_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _wallet_cash() -> float | None:
    try:
        from polymarket_wallet import fetch_wallet_status

        return float(fetch_wallet_status().get("cash_usdc") or 0)
    except Exception:
        return None


def settle_open_trades() -> int:
    rows = load_trades()
    changed = 0
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "open":
            out.append(row)
            continue
        slug = str(row.get("slug") or "")
        res = fetch_market_outcome(slug)
        if not res or not res.get("closed"):
            out.append(row)
            continue
        outcome = str(res.get("outcome") or "").lower()
        side = str(row.get("side") or "").lower()
        won = outcome == side
        stake = float(row.get("stake") or STAKE_USD)
        ask = float(row.get("fill_ask") or row.get("ask") or 0.5)
        pnl = calc_win_pnl(stake, ask) if won else calc_loss_pnl(stake)
        row["status"] = "settled"
        row["outcome"] = outcome
        row["won"] = won
        row["trade_result"] = trade_result(won)
        row["pnl"] = round(pnl, 2)
        row["settled_at_hkt"] = now_hkt_str()
        changed += 1
        append_event(
            {
                "event": "trade_settled",
                "slug": slug,
                "pnl": row["pnl"],
                "won": won,
                "side": side,
            }
        )
        out.append(row)
    if changed:
        save_trades(out)
    return changed


def _place_live_buy(
    *,
    market,
    side: str,
    stake: float,
    decision_ask: float,
    slug: str,
    prior_range_usd: float,
    t_remaining: float,
) -> dict[str, Any]:
    from btc_clob import compute_max_fill_price, place_live_buy_confirmed
    from paper_engine import _apply_live_fill

    token = market.up_token_id if side == "up" else market.down_token_id
    max_fill = min(0.88, float(decision_ask) + 0.03)
    max_fill = compute_max_fill_price(decision_ask, max_fill)
    cash = _wallet_cash() or 0.0
    row: dict[str, Any] = {
        "asset": "BTC",
        "slug": slug,
        "side": side,
        "contract": side_to_contract(side),
        "ask": round(decision_ask, 4),
        "fill_ask": round(decision_ask, 4),
        "stake": stake,
        "stake_intended": stake,
        "status": "open",
        "strategy": STRATEGY_ID,
        "strategy_label": STRATEGY_LABEL,
        "prior_range_usd": prior_range_usd,
        "t_remaining": round(t_remaining, 1),
        "session_id": ensure_session().get("session_id"),
        "opened_at_hkt": now_hkt_str(),
        "trading_mode": "live",
        "validated_warning": True,
    }
    order = place_live_buy_confirmed(
        token_id=token,
        stake_usd=stake,
        decision_ask=decision_ask,
        max_fill_price=max_fill,
    )
    filled = _apply_live_fill(row, order, stake, cash)
    if not filled:
        row["status"] = "unfilled"
        append_event({"event": "entry_failed", "slug": slug, "error": row.get("strategy_reason")})
        return row
    row["status"] = "open"
    append_event({"event": "entry", "slug": slug, "side": side, "ask": decision_ask, "stake": stake})
    return row


def try_entry(
    *,
    market,
    decision: dict[str, Any],
    t_remaining: float,
) -> dict[str, Any] | None:
    rows_existing = load_trades()
    if any(
        t.get("slug") == market.slug and t.get("status") in ("open", "settled", "unfilled")
        for t in rows_existing
    ):
        return None
    side = str(decision["side"])
    stake = float(decision.get("stake_usd") or STAKE_USD)
    ask = float(decision["ask"])
    mode = trading_mode()
    if mode == "paper" or dry_run():
        if mode == "live" and not live_armed():
            append_event(
                {
                    "event": "wait_capital",
                    "slug": market.slug,
                    "side": side,
                    "ask": ask,
                    "note": "MIDVOL_LIVE_ARMED=0 — 入金後設 1 先落真單",
                }
            )
            return None
        row = {
            "asset": "BTC",
            "slug": market.slug,
            "side": side,
            "contract": side_to_contract(side),
            "ask": round(ask, 4),
            "fill_ask": round(ask, 4),
            "stake": stake,
            "status": "open",
            "strategy": STRATEGY_ID,
            "simulated": True,
            "session_id": ensure_session().get("session_id"),
            "opened_at_hkt": now_hkt_str(),
            "prior_range_usd": decision.get("prior_range_usd"),
            "t_remaining": round(t_remaining, 1),
        }
        append_event({"event": "entry_paper", "slug": market.slug, "side": side, "ask": ask})
        rows = load_trades()
        rows.append(row)
        save_trades(rows)
        return row
    cash = _wallet_cash()
    if cash is not None and cash < stake:
        append_event({"event": "skip_low_cash", "slug": market.slug, "cash": cash, "stake": stake})
        return None
    row = _place_live_buy(
        market=market,
        side=side,
        stake=stake,
        decision_ask=ask,
        slug=market.slug,
        prior_range_usd=float(decision.get("prior_range_usd") or 0),
        t_remaining=t_remaining,
    )
    if row.get("status") == "open":
        rows = load_trades()
        rows.append(row)
        save_trades(rows)
        return row
    return None


def dashboard_snapshot() -> dict[str, Any]:
    sess = ensure_session()
    rows = load_trades()
    settled = [r for r in rows if r.get("status") == "settled"]
    open_n = sum(1 for r in rows if r.get("status") == "open")
    pnl = round(sum(float(r.get("pnl") or 0) for r in settled), 2)
    wins = sum(1 for r in settled if r.get("won"))
    losses = len(settled) - wins
    cash = _wallet_cash()
    return {
        "ok": True,
        "session": sess,
        "rules": rules_summary(),
        "trading_mode": trading_mode(),
        "live_armed": live_armed(),
        "dry_run": dry_run(),
        "wallet_cash_usdc": cash,
        "stats": {
            "trades_total": len(rows),
            "open": open_n,
            "settled": len(settled),
            "wins": wins,
            "losses": losses,
            "pnl_usd": pnl,
        },
        "validated_warning": "未獨立驗證；請先入金前確認 MIDVOL_LIVE_ARMED",
    }
