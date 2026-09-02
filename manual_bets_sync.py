"""Detect Polymarket wallet trades not placed by bots → sync into manual_bets.json."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from bot_common import ETH_LIVE_TRADE_LOG_FILE, LIVE_TRADE_LOG_FILE
from manual_bets import MANUAL_BETS_FILE, HKT, load_manual_bets

ROOT = Path("/root/btc-5m-bot")
SYNC_STATE_FILE = ROOT / "logs" / "manual_bets_sync_state.json"


def _load_env_wallet() -> str:
    addr = os.getenv("POLYMARKET_WALLET_ADDRESS", "").strip()
    if addr:
        return addr
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("POLYMARKET_WALLET_ADDRESS="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""
ACTIVITY_URL = "https://data-api.polymarket.com/activity"
SYNC_COOLDOWN_SEC = 90
SYNC_COOLDOWN_OPEN_SEC = 30
STAKE_TOLERANCE_USD = 2.0
STAKE_TOLERANCE_PCT = 0.15
MIN_MANUAL_STAKE_USD = 5.0


def _wallet_address() -> str:
    addr = _load_env_wallet()
    if not addr:
        raise RuntimeError("POLYMARKET_WALLET_ADDRESS not set")
    return addr


def _ts_hkt(ts: int | float) -> str:
    return datetime.fromtimestamp(float(ts), HKT).strftime("%Y-%m-%d %H:%M:%S")


def _norm_side(side: str | None) -> str:
    s = (side or "").strip().lower()
    if s in ("up", "yes"):
        return "up"
    if s in ("down", "no"):
        return "down"
    return s


def _stake_matches(bot_stake: float, activity_usdc: float) -> bool:
    if bot_stake <= 0 or activity_usdc <= 0:
        return False
    tol = max(STAKE_TOLERANCE_USD, bot_stake * STAKE_TOLERANCE_PCT)
    return abs(bot_stake - activity_usdc) <= tol


def _load_bot_trades() -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return entered bot trades + slug → resolved outcome label (Up/Down)."""
    trades: list[dict[str, Any]] = []
    resolved: dict[str, str] = {}
    for path in (LIVE_TRADE_LOG_FILE, ETH_LIVE_TRADE_LOG_FILE):
        if not path.exists():
            continue
        try:
            rows = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for row in rows:
            if not row.get("entered"):
                continue
            slug = str(row.get("slug") or "")
            if not slug:
                continue
            stake = float(row.get("stake") or 0)
            side = _norm_side(row.get("side") or row.get("bought_contract"))
            tx_hashes = set()
            live_order = row.get("live_order") if isinstance(row.get("live_order"), dict) else {}
            for tx in live_order.get("transactionsHashes") or live_order.get("transactionHashes") or []:
                if tx:
                    tx_hashes.add(str(tx).lower())
            for tx in row.get("transactionsHashes") or row.get("transaction_hashes") or []:
                if tx:
                    tx_hashes.add(str(tx).lower())
            trades.append(
                {
                    "slug": slug,
                    "side": side,
                    "stake": stake,
                    "timestamp_hkt": row.get("timestamp_hkt"),
                    "log": path.name,
                    "matched": False,
                    "tx_hashes": tx_hashes,
                }
            )
            ro = row.get("resolved_outcome") or row.get("resolved_label")
            if ro and slug not in resolved:
                label = str(ro).upper()
                if label in ("UP", "YES"):
                    resolved[slug] = "Up"
                elif label in ("DOWN", "NO"):
                    resolved[slug] = "Down"
    return trades, resolved


def _fetch_activity(*, limit: int = 500, max_pages: int = 4) -> list[dict[str, Any]]:
    addr = _wallet_address()
    out: list[dict[str, Any]] = []
    offset = 0
    for _ in range(max_pages):
        resp = requests.get(
            ACTIVITY_URL,
            params={"user": addr, "limit": limit, "offset": offset},
            timeout=25,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        out.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return out


def _group_activity(activities: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in activities:
        slug = str(row.get("slug") or "")
        if slug:
            by_slug[slug].append(row)
    return by_slug


def _infer_winner(
    slug: str,
    slug_rows: list[dict[str, Any]],
    resolved_by_slug: dict[str, str],
) -> str | None:
    if slug in resolved_by_slug:
        return resolved_by_slug[slug]
    try:
        from polymarket_crypto import fetch_market_outcome

        mo = fetch_market_outcome(slug)
        if mo and mo.get("closed") and mo.get("outcome") in ("up", "down"):
            return "Up" if mo["outcome"] == "up" else "Down"
    except Exception:
        pass
    trades = [r for r in slug_rows if r.get("type") == "TRADE"]
    redeems = [r for r in slug_rows if r.get("type") == "REDEEM"]
    if not trades or not redeems:
        return None
    redeem_total = sum(float(r.get("usdcSize") or 0) for r in redeems)
    if redeem_total <= 0:
        return None
    best_outcome = None
    best_diff = float("inf")
    for outcome in ("Up", "Down"):
        stake = sum(
            float(r.get("usdcSize") or 0)
            for r in trades
            if (r.get("outcome") or "") == outcome
        )
        if stake <= 0:
            continue
        diff = abs(redeem_total - stake)
        if diff < best_diff:
            best_diff = diff
            best_outcome = outcome
    return best_outcome


def _trade_row_key(trade: dict[str, Any]) -> str:
    tx = str(trade.get("transactionHash") or "")
    if tx:
        return tx
    slug = str(trade.get("slug") or "")
    return f"{slug}:{trade.get('timestamp')}:{trade.get('outcome')}"


def _compute_manual_pnls_for_slug(
    manual_trades: list[dict[str, Any]],
    slug_rows: list[dict[str, Any]],
    winner: str | None,
) -> dict[str, tuple[str, float | None, float | None]]:
    """PnL per manual TRADE row; splits REDEEM across winning legs on same slug."""
    out: dict[str, tuple[str, float | None, float | None]] = {}
    if not winner:
        for trade in manual_trades:
            out[_trade_row_key(trade)] = ("OPEN", None, None)
        return out

    redeem_total = sum(
        float(r.get("usdcSize") or 0) for r in slug_rows if r.get("type") == "REDEEM"
    )
    winning: list[tuple[dict[str, Any], float, float]] = []
    for trade in manual_trades:
        key = _trade_row_key(trade)
        stake = round(float(trade.get("usdcSize") or 0), 2)
        outcome = trade.get("outcome") or ""
        if outcome != winner:
            out[key] = ("LOSS", round(-stake, 2), 0.0)
            continue
        price = float(trade.get("price") or 0)
        shares = stake / price if price > 0 else 0.0
        winning.append((trade, stake, shares))

    total_shares = sum(sh for _, _, sh in winning)
    for trade, stake, shares in winning:
        key = _trade_row_key(trade)
        if redeem_total > 0 and total_shares > 0:
            redeem = round(redeem_total * shares / total_shares, 2)
        else:
            price = float(trade.get("price") or 0)
            redeem = round(stake / price, 2) if price > 0 else stake
        out[key] = ("WIN", round(redeem - stake, 2), redeem)
    return out


def _is_bot_trade(
    trade: dict[str, Any],
    bot_trades: list[dict[str, Any]],
) -> bool:
    slug = str(trade.get("slug") or "")
    side = _norm_side(trade.get("outcome"))
    usdc = float(trade.get("usdcSize") or 0)
    tx = str(trade.get("transactionHash") or "").lower()
    if tx:
        for bot in bot_trades:
            if tx in (bot.get("tx_hashes") or set()):
                return True
    candidates = [
        b
        for b in bot_trades
        if not b["matched"] and b["slug"] == slug and b["side"] == side
    ]
    for bot in sorted(candidates, key=lambda x: x.get("timestamp_hkt") or ""):
        if _stake_matches(bot["stake"], usdc):
            bot["matched"] = True
            return True
    return False


def _bet_key(bet: dict[str, Any]) -> str:
    if bet.get("transaction_hash"):
        return f"tx:{bet['transaction_hash']}"
    return f"id:{bet.get('id')}"


def _same_bet(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("transaction_hash") and a.get("transaction_hash") == b.get("transaction_hash"):
        return True
    if a.get("slug") and a.get("slug") == b.get("slug"):
        if _norm_side(a.get("side")) == _norm_side(b.get("side")):
            sa = float(a.get("stake_usd") or 0)
            sb = float(b.get("stake_usd") or 0)
            if _stake_matches(sa, sb):
                return True
    if _norm_side(a.get("side")) == _norm_side(b.get("side")):
        sa = float(a.get("stake_usd") or 0)
        sb = float(b.get("stake_usd") or 0)
        if sa > 0 and _stake_matches(sa, sb):
            day_a = str(a.get("timestamp_hkt") or "")[:10]
            day_b = str(b.get("timestamp_hkt") or "")[:10]
            if day_a and day_a == day_b:
                return True
    return False


def _merge_bet(existing: dict[str, Any], detected: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key in (
        "title",
        "timestamp_hkt",
        "side",
        "stake_usd",
        "entry_price",
        "redeem_usd",
        "pnl_usd",
        "result",
        "slug",
        "transaction_hash",
        "detection_reason",
        "auto_detected",
        "excluded_from_bot",
    ):
        if detected.get(key) is not None:
            merged[key] = detected[key]
    if existing.get("source") in ("polymarket_app", "user_reported", "manual"):
        merged["confirmation"] = "auto_confirmed"
        merged["prior_source"] = existing.get("source")
        merged["source"] = "manual"
        merged["label"] = existing.get("label") or detected.get("label")
        merged["id"] = existing.get("id") or detected.get("id")
        merged["trade_type"] = "manual"
    else:
        merged["source"] = detected.get("source") or merged.get("source") or "manual"
        merged["confirmation"] = detected.get("confirmation") or merged.get("confirmation")
        merged["trade_type"] = "manual"
    if existing.get("note") and not detected.get("note"):
        merged["note"] = existing["note"]
    merged["excluded_from_bot"] = True
    merged["updated_at_hkt"] = _ts_hkt(time.time())
    return merged


def _load_sync_state() -> dict[str, Any]:
    if not SYNC_STATE_FILE.exists():
        return {}
    try:
        return json.loads(SYNC_STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_sync_state(state: dict[str, Any]) -> None:
    SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_FILE.write_text(json.dumps(state, indent=2))


def save_manual_bets_file(
    bets: list[dict[str, Any]],
    *,
    note: str | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> None:
    existing_meta = {}
    if MANUAL_BETS_FILE.exists():
        try:
            raw = json.loads(MANUAL_BETS_FILE.read_text())
            if isinstance(raw, dict):
                existing_meta = {
                    k: raw[k]
                    for k in ("pnl_baseline_hkt", "archived_tx_hashes", "archived_bet_count")
                    if k in raw
                }
        except json.JSONDecodeError:
            pass
    payload: dict[str, Any] = {
        "version": 1,
        "note": note or "Manual bets — excluded from BTC/ETH bot PnL. Auto-detected + user-reported.",
        "bets": bets,
        "last_sync_hkt": _ts_hkt(time.time()),
        **existing_meta,
    }
    if extra_meta:
        payload.update(extra_meta)
    MANUAL_BETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANUAL_BETS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def reset_manual_bets_for_capital_launch(launch_hkt: str) -> dict[str, Any]:
    """Archive pre-launch manual bets for backtest; dashboard PnL starts at zero."""
    existing = load_manual_bets()
    archived_hashes = sorted(
        {
            str(b.get("transaction_hash") or b.get("id") or "")
            for b in existing
            if b.get("transaction_hash") or b.get("id")
        }
    )
    save_manual_bets_file(
        [],
        note=f"PnL reset at capital launch {launch_hkt}. Pre-launch manual bets archived for backtest.",
        extra_meta={
            "pnl_baseline_hkt": launch_hkt,
            "archived_tx_hashes": archived_hashes,
            "archived_bet_count": len(existing),
        },
    )
    _save_sync_state(
        {
            "last_sync_ts": time.time(),
            "baseline_hkt": launch_hkt,
            "archived_tx_count": len(archived_hashes),
        }
    )
    return {"archived_bet_count": len(existing), "archived_tx_hashes": len(archived_hashes)}


def _manual_activity_skipped(row: dict[str, Any], *, baseline_hkt: str | None, archived: set[str]) -> bool:
    tx = str(row.get("transactionHash") or "")
    if tx and tx in archived:
        return True
    if baseline_hkt:
        ts_hkt = _ts_hkt(row.get("timestamp") or 0)
        if ts_hkt < baseline_hkt:
            return True
    return False


def _open_manual_count() -> int:
    return sum(1 for b in load_manual_bets() if b.get("result") == "OPEN")


def maybe_sync_manual_bets(*, force: bool = False) -> dict[str, Any]:
    """Auto-sync with shorter cooldown while OPEN manual bets exist."""
    state = _load_sync_state()
    now = time.time()
    last = float(state.get("last_sync_ts") or 0)
    open_n = _open_manual_count()
    cooldown = SYNC_COOLDOWN_OPEN_SEC if open_n else SYNC_COOLDOWN_SEC
    if not force and now - last < cooldown:
        summary = manual_bets_summary_safe(auto_sync=False)
        return {
            "skipped": True,
            "cooldown_sec": cooldown,
            "open_count": open_n,
            "next_sync_in_sec": max(0, int(cooldown - (now - last))),
            **summary,
        }
    return sync_manual_bets_from_activity()


def sync_manual_bets_if_due(*, logger: Any = None, reason: str = "") -> dict[str, Any] | None:
    """Called from observers after window settle; logs changes when logger provided."""
    try:
        result = maybe_sync_manual_bets()
    except Exception as exc:
        if logger is not None:
            logger.warning("Manual bets sync failed (%s): %s", reason or "observer", exc)
        return None
    if logger is not None and not result.get("skipped"):
        if result.get("added") or result.get("settled") or result.get("updated"):
            logger.info(
                "Manual bets sync (%s): +%s new, %s settled, %s updated, open=%s daily_pnl=%s",
                reason or "observer",
                result.get("added", 0),
                result.get("settled", 0),
                result.get("updated", 0),
                result.get("open_count", 0),
                result.get("daily_pnl_hkt", 0),
            )
    return result


def sync_manual_bets_from_activity() -> dict[str, Any]:
    """Fetch wallet activity; mark non-bot TRADE rows as manual bets."""
    now = time.time()
    existing_before = {b.get("transaction_hash") or b.get("id"): b for b in load_manual_bets()}
    from manual_bets import load_manual_bets_meta

    meta = load_manual_bets_meta()
    baseline_hkt = meta.get("pnl_baseline_hkt")
    archived = set(meta.get("archived_tx_hashes") or [])

    bot_trades, resolved_by_slug = _load_bot_trades()
    activities = _fetch_activity()
    by_slug = _group_activity(activities)

    manual_rows: list[dict[str, Any]] = []
    for row in activities:
        if row.get("type") != "TRADE":
            continue
        usdc = float(row.get("usdcSize") or 0)
        if usdc < MIN_MANUAL_STAKE_USD:
            continue
        if _is_bot_trade(row, bot_trades):
            continue
        if _manual_activity_skipped(row, baseline_hkt=baseline_hkt, archived=archived):
            continue
        manual_rows.append(row)

    slug_manual: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in manual_rows:
        slug_manual[str(row.get("slug") or "")].append(row)

    slug_winners: dict[str, str | None] = {}
    slug_pnls: dict[str, dict[str, tuple[str, float | None, float | None]]] = {}
    for slug, rows in slug_manual.items():
        slug_rows = by_slug.get(slug, [])
        winner = _infer_winner(slug, slug_rows, resolved_by_slug)
        slug_winners[slug] = winner
        slug_pnls[slug] = _compute_manual_pnls_for_slug(rows, slug_rows, winner)

    detected: list[dict[str, Any]] = []
    manual_idx = 0
    for row in manual_rows:
        slug = str(row.get("slug") or "")
        key = _trade_row_key(row)
        result, pnl, redeem = slug_pnls.get(slug, {}).get(key, ("OPEN", None, None))
        usdc = float(row.get("usdcSize") or 0)
        manual_idx += 1
        tx = str(row.get("transactionHash") or "")
        side = row.get("outcome") or "—"
        detected.append(
            {
                "id": f"auto-{tx[2:18]}" if tx else f"auto-{slug}-{int(row.get('timestamp') or 0)}",
                "label": f"Manual #{manual_idx}",
                "title": row.get("title") or slug,
                "timestamp_hkt": _ts_hkt(row.get("timestamp") or 0),
                "side": side,
                "stake_usd": round(usdc, 2),
                "entry_price": round(float(row.get("price") or 0), 4) or None,
                "redeem_usd": redeem,
                "pnl_usd": pnl,
                "result": result,
                "slug": slug,
                "transaction_hash": tx or None,
                "source": "manual",
                "confirmation": "auto",
                "detection_reason": "not_in_bot_log",
                "auto_detected": True,
                "excluded_from_bot": True,
                "trade_type": "manual",
            }
        )

    existing = load_manual_bets()
    preserved = [
        ex
        for ex in existing
        if ex.get("source") in ("polymarket_app", "user_reported")
        or ex.get("confirmation") == "auto_confirmed"
    ]
    merged: list[dict[str, Any]] = []
    used_detected: set[int] = set()
    replaced_ids: list[str] = []

    for ex in preserved:
        match_i = None
        for i, det in enumerate(detected):
            if i in used_detected:
                continue
            if _same_bet(ex, det):
                match_i = i
                break
        if match_i is not None:
            merged.append(_merge_bet(ex, detected[match_i]))
            used_detected.add(match_i)
            if ex.get("source") != "auto_activity_api":
                replaced_ids.append(str(ex.get("id")))
        elif ex.get("id") == "manual-20260623-loss":
            placeholder_day = str(ex.get("timestamp_hkt") or "")[:10]
            superseded = any(
                d.get("slug") == "btc-updown-5m-1782202800"
                and str(d.get("timestamp_hkt") or "")[:10] == placeholder_day
                for i, d in enumerate(detected)
                if i not in used_detected
            )
            if superseded:
                replaced_ids.append(ex["id"])
                continue
            merged.append(ex)
        else:
            merged.append(ex)

    for i, det in enumerate(detected):
        if i in used_detected:
            continue
        merged.append(det)

    merged.sort(key=lambda b: b.get("timestamp_hkt") or "", reverse=True)
    save_manual_bets_file(merged)

    added = len(detected) - len(used_detected)
    settled = updated = 0
    merged_by_key = {b.get("transaction_hash") or b.get("id"): b for b in merged}
    for key, old in existing_before.items():
        if not key:
            continue
        new = merged_by_key.get(key)
        if not new:
            continue
        if old.get("result") == "OPEN" and new.get("result") in ("WIN", "LOSS"):
            settled += 1
        elif old.get("result") in ("WIN", "LOSS") and new.get("result") in ("WIN", "LOSS"):
            if old.get("pnl_usd") != new.get("pnl_usd"):
                updated += 1

    summary = manual_bets_summary_safe(auto_sync=False)
    open_count = sum(1 for b in merged if b.get("result") == "OPEN")
    sync_result = {
        "skipped": False,
        "detected": len(detected),
        "merged": len(used_detected),
        "added": added,
        "settled": settled,
        "updated": updated,
        "open_count": open_count,
        "replaced_placeholders": replaced_ids,
        "synced_at_hkt": _ts_hkt(now),
        **summary,
    }
    _save_sync_state({"last_sync_ts": now, "last_result": sync_result})
    return sync_result


def manual_bets_summary_safe(*, auto_sync: bool = False) -> dict[str, Any]:
    from manual_bets import manual_bets_summary

    return manual_bets_summary(auto_sync=auto_sync)
