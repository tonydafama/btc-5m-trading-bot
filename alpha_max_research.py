"""Alpha-Max research data — window signals, cell stats, stake sizing proposals."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot_common import LOG_DIR as V1_LOG_DIR
from bot_instance import log_root
from new_strategy import calc_loss_pnl, calc_win_pnl, now_hkt_str, trade_result, side_to_contract

from alpha_max import (
    ALPHA_CELLS,
    STAKE_RULES,
    _candidate,
    _timing_ok,
    ensure_session,
    load_alpha_trades,
    period_for_dt,
    pick_alpha_candidate,
    stake_for,
    tier_for_ask,
    _dt_from_snapshot,
)

RESEARCH_FILE = log_root() / "alpha_max_research.jsonl"
RESEARCH_CACHE = log_root() / "alpha_max_research_summary.json"

CHECKPOINT_ORDER = [75, 60, 50, 40, 30, 25, 12, 5]

# Counterfactual stake multipliers vs current rules (for sizing research).
STAKE_SCENARIO_MULTS = [0.5, 0.75, 1.0, 1.25, 1.5]

# Proposed live caps (Bot #2 ~$500–800 wallet at launch).
PROPOSED_WALLET_CAP_PCT = 0.05
PROPOSED_CORE_WALLET_MULT_BASE = 800.0


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _checkpoint_sort_key(snapshot: dict[str, Any]) -> tuple[int, float]:
    cp = snapshot.get("checkpoint") or ""
    text = str(cp).replace("T-", "")
    try:
        t = int(text)
    except ValueError:
        t = 999
    return (CHECKPOINT_ORDER.index(t) if t in CHECKPOINT_ORDER else 99, float(snapshot.get("ask") or 99))


def pick_alpha_candidate_live_style(asset: str, checkpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First eligible checkpoint in live observer order (75 → 5)."""
    ordered = sorted(checkpoints, key=_checkpoint_sort_key)
    for snap in ordered:
        cand = _candidate(asset.upper(), snap)
        if cand:
            return cand
    return None


def _est_fee(stake: float, ask: float) -> float:
    from polymarket_fees import estimate_taker_fee

    if ask <= 0 or stake <= 0:
        return 0.0
    return estimate_taker_fee(stake / ask, ask)


def _simulate_pnl(stake: float, ask: float, won: bool) -> float:
    if stake <= 0 or ask <= 0:
        return 0.0
    gross = calc_win_pnl(stake, ask) if won else calc_loss_pnl(stake)
    return round(gross - _est_fee(stake, ask), 2)


def _cell_key(cand: dict[str, Any]) -> str:
    return f"{cand['asset']}|{cand['period']}|{cand['tier']}|{cand['hour_et']:02d}"


def _primary_skip_reason(asset: str, checkpoints: list[dict[str, Any]]) -> str:
    if not checkpoints:
        return "no_checkpoints"
    had_ask = False
    had_timing = False
    had_tier = False
    had_cell = False
    for snap in checkpoints:
        ask = snap.get("ask")
        if ask is None:
            continue
        ask = float(ask)
        if ask <= 0 or ask > 0.90:
            continue
        had_ask = True
        if not _timing_ok(snap):
            continue
        had_timing = True
        tier = tier_for_ask(ask)
        if tier == "skip":
            continue
        had_tier = True
        dt_et = _dt_from_snapshot(snap)
        cell = (asset.upper(), period_for_dt(dt_et), tier, dt_et.hour)
        if cell in ALPHA_CELLS:
            had_cell = True
    if had_cell:
        return "eligible"
    if had_tier:
        return "cell_not_in_alpha"
    if had_timing:
        return "tier_skip"
    if had_ask:
        return "timing_or_ask_cap"
    return "no_valid_ask"


def record_window_evaluation(
    *,
    asset: str,
    slug: str,
    title: str,
    checkpoints: list[dict[str, Any]],
    outcome: str | None,
    source: str = "observer",
) -> dict[str, Any]:
    """Persist one window evaluation (trade or skip) for sizing research."""
    asset = asset.upper()
    paper = pick_alpha_candidate(asset, checkpoints)
    live = pick_alpha_candidate_live_style(asset, checkpoints)
    skip = _primary_skip_reason(asset, checkpoints)
    resolved = None
    if outcome in ("up", "down"):
        resolved = "UP" if outcome == "up" else "DOWN"

    row: dict[str, Any] = {
        "timestamp_hkt": now_hkt_str(),
        "source": source,
        "asset": asset,
        "slug": slug,
        "market": title,
        "outcome": outcome,
        "n_checkpoints": len(checkpoints),
        "skip_primary": skip,
        "paper_would_enter": paper is not None,
        "live_would_enter": live is not None,
        "picks_match": (
            paper is not None and live is not None
            and _cell_key(paper) == _cell_key(live)
            and abs(float(paper["ask"]) - float(live["ask"])) < 0.0001
        ) if paper and live else paper is None and live is None,
    }
    if paper:
        row["paper_cell"] = _cell_key(paper)
        row["paper_ask"] = round(float(paper["ask"]), 4)
        row["paper_checkpoint"] = paper.get("checkpoint")
        stake = stake_for(asset, paper["tier"], paper["period"])
        row["paper_stake"] = stake
        row["paper_est_fee"] = round(_est_fee(stake, float(paper["ask"])), 2)
        if resolved:
            contract = side_to_contract(paper["side"])
            result = trade_result(resolved, contract, True)
            won = result == "WIN"
            row["paper_result"] = result
            row["paper_pnl_gross"] = round(
                calc_win_pnl(stake, float(paper["ask"])) if won else calc_loss_pnl(stake), 2
            )
            row["paper_pnl_net"] = _simulate_pnl(stake, float(paper["ask"]), won)
    if live and (not paper or _cell_key(live) != _cell_key(paper) or live.get("checkpoint") != paper.get("checkpoint")):
        row["live_cell"] = _cell_key(live)
        row["live_ask"] = round(float(live["ask"]), 4)
        row["live_checkpoint"] = live.get("checkpoint")
        if resolved:
            contract = side_to_contract(live["side"])
            result = trade_result(resolved, contract, True)
            won = result == "WIN"
            stake_l = stake_for(asset, live["tier"], live["period"])
            row["live_pnl_net"] = _simulate_pnl(stake_l, float(live["ask"]), won)

    try:
        from trade_analytics import checkpoint_trail

        trail = checkpoint_trail(checkpoints)
        if trail:
            row["checkpoint_trail"] = trail
    except Exception:
        pass

    if source == "live":
        try:
            from alpha_max_live import _latest_live_row_for_slug

            actual = _latest_live_row_for_slug(slug)
            if actual:
                row["actual_entered"] = bool(actual.get("filled"))
                row["actual_trade_result"] = actual.get("trade_result")
                row["actual_cell"] = actual.get("cell_key")
                row["actual_stake"] = actual.get("stake")
                row["actual_fill_price"] = actual.get("fill_price") or actual.get("entry_ask")
                row["actual_gross_pnl"] = actual.get("gross_pnl")
                row["actual_net_pnl"] = actual.get("net_pnl", actual.get("pnl"))
                row["actual_est_fee"] = actual.get("est_fee_usd")
                row["actual_slippage"] = actual.get("fill_slippage")
        except Exception:
            pass

    _append_jsonl(RESEARCH_FILE, row)
    return row


def stake_for_proposed(
    asset: str,
    tier: str,
    period: str,
    *,
    wallet: float,
) -> float:
    """Proposed Bot #2 sizing: cap % of wallet, softer core scaling."""
    rules = STAKE_RULES[asset.upper()]
    base = float(rules[tier])
    stake = base
    if period == "core":
        stake *= rules["core_mult"] * min(2.0, max(1.0, wallet / PROPOSED_CORE_WALLET_MULT_BASE))
    cap = round(wallet * PROPOSED_WALLET_CAP_PCT, 2)
    if tier == "underdog":
        cap = min(cap, round(wallet * 0.08, 2))
    stake = min(stake, cap) if cap > 0 else stake
    return round(max(stake, 5.0), 2)


def _cell_stats_from_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_cell: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("trade_result") not in ("WIN", "LOSS"):
            continue
        key = f"{r.get('asset')}|{r.get('period')}|{r.get('tier')}|{int(r.get('hour_et') or 0):02d}"
        by_cell[key].append(r)

    out: list[dict[str, Any]] = []
    for key, trades in sorted(by_cell.items()):
        wins = sum(1 for t in trades if t.get("trade_result") == "WIN")
        n = len(trades)
        gross = round(sum(float(t.get("gross_pnl") or t.get("pnl") or 0) for t in trades), 2)
        stakes = [float(t.get("stake") or 0) for t in trades]
        asks = [float(t.get("entry_ask") or 0) for t in trades if t.get("entry_ask")]
        fees = round(
            sum(
                float(t.get("est_fee_usd"))
                if t.get("est_fee_usd") is not None
                else _est_fee(float(t.get("stake") or 0), float(t.get("entry_ask") or 0.5))
                for t in trades
            ),
            2,
        )
        net = round(gross - fees, 2)
        parts = key.split("|")
        out.append({
            "cell": key,
            "asset": parts[0],
            "period": parts[1],
            "tier": parts[2],
            "hour_et": int(parts[3]),
            "trades": n,
            "wins": wins,
            "losses": n - wins,
            "win_rate": round(100 * wins / n, 1) if n else None,
            "gross_pnl": gross,
            "est_fees": fees,
            "net_pnl": net,
            "ev_gross_per_trade": round(gross / n, 2) if n else None,
            "ev_net_per_trade": round(net / n, 2) if n else None,
            "avg_stake": round(statistics.mean(stakes), 2) if stakes else None,
            "avg_ask": round(statistics.mean(asks), 4) if asks else None,
            "in_alpha_cells": (parts[0], parts[1], parts[2], int(parts[3])) in ALPHA_CELLS,
        })
    return sorted(out, key=lambda x: (-x["trades"], x["cell"]))


def _stake_scenarios(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closed = [r for r in rows if r.get("trade_result") in ("WIN", "LOSS")]
    if not closed:
        return []
    scenarios: list[dict[str, Any]] = []
    for mult in STAKE_SCENARIO_MULTS:
        gross = 0.0
        fees = 0.0
        for r in closed:
            ask = float(r.get("entry_ask") or 0.5)
            stake = round(float(r.get("stake") or 0) * mult, 2)
            won = r.get("trade_result") == "WIN"
            gross += calc_win_pnl(stake, ask) if won else calc_loss_pnl(stake)
            fees += _est_fee(stake, ask)
        net = round(gross - fees, 2)
        scenarios.append({
            "mult": mult,
            "label": f"{int(mult * 100)}% of current rules",
            "gross_pnl": round(gross, 2),
            "est_fees": round(fees, 2),
            "net_pnl": net,
            "ev_net_per_trade": round(net / len(closed), 2),
        })
    return scenarios


def _go_live_readiness() -> dict[str, Any]:
    bot2_env = Path("/root/btc-5m-bot/.env.bot2")
    creds_ok = False
    if bot2_env.exists():
        text = bot2_env.read_text()
        creds_ok = "POLYMARKET_PRIVATE_KEY=" in text and not text.split("POLYMARKET_PRIVATE_KEY=")[1].split("\n")[0].strip() == ""
    from bot_instance import log_root

    log_dir = log_root()
    sess_path = log_dir / "alpha_max_live_session.json"
    sess = _load_json(sess_path, {})
    trades = load_alpha_trades()
    closed = [t for t in trades if t.get("trade_result") in ("WIN", "LOSS")]
    eth_closed = [t for t in closed if str(t.get("asset")).upper() == "ETH"]
    ud = [t for t in closed if t.get("tier") == "underdog"]

    checks = [
        {"id": "paper_trades", "label": "Paper ≥ 50 closed", "ok": len(closed) >= 50, "detail": f"{len(closed)} closed"},
        {"id": "paper_days", "label": "Paper ≥ 3 calendar days", "ok": _research_span_days() >= 3, "detail": f"{_research_span_days()} days"},
        {"id": "eth_samples", "label": "ETH paper samples (target ≥ 5)", "ok": len(eth_closed) >= 5, "detail": f"{len(eth_closed)} ETH trades"},
        {"id": "underdog_samples", "label": "Underdog samples (target ≥ 3)", "ok": len(ud) >= 3, "detail": f"{len(ud)} underdog trades"},
        {"id": "research_file", "label": "Window research log", "ok": RESEARCH_FILE.exists(), "detail": _research_line_count()},
        {"id": "bot2_creds", "label": "Bot #2 credentials", "ok": creds_ok, "detail": "fill .env.bot2 + setup_bot2_credentials.py"},
        {"id": "live_session", "label": "Live session started", "ok": bool(sess.get("session_id")), "detail": sess.get("session_id") or "not started"},
    ]
    return {
        "ready_count": sum(1 for c in checks if c["ok"]),
        "total_checks": len(checks),
        "checks": checks,
        "launch_blockers": [c["label"] for c in checks if not c["ok"] and c["id"] in ("bot2_creds", "live_session")],
    }


def _research_line_count() -> str:
    if not RESEARCH_FILE.exists():
        return "0 windows"
    n = sum(1 for _ in RESEARCH_FILE.open())
    return f"{n} windows logged"


def _research_span_days() -> int:
    rows = load_alpha_trades()
    days = {str(r.get("timestamp_hkt", ""))[:10] for r in rows if r.get("timestamp_hkt")}
    return len(days)


def _research_aggregate() -> dict[str, Any]:
    if not RESEARCH_FILE.exists():
        return {"windows_logged": 0}
    skip_counter: Counter = Counter()
    paper_enter = live_enter = match = diverge = 0
    n = 0
    for line in RESEARCH_FILE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        n += 1
        skip_counter[row.get("skip_primary") or "unknown"] += 1
        if row.get("paper_would_enter"):
            paper_enter += 1
        if row.get("live_would_enter"):
            live_enter += 1
        if row.get("picks_match"):
            match += 1
        elif row.get("paper_would_enter") or row.get("live_would_enter"):
            diverge += 1
    return {
        "windows_logged": n,
        "paper_entry_windows": paper_enter,
        "live_entry_windows": live_enter,
        "pick_match": match,
        "pick_diverge": diverge,
        "skip_reasons": dict(skip_counter.most_common(12)),
    }


def build_research_report(*, refresh_cache: bool = True) -> dict[str, Any]:
    ensure_session()
    trades = load_alpha_trades()
    closed = [t for t in trades if t.get("trade_result") in ("WIN", "LOSS")]
    cells = _cell_stats_from_trades(trades)
    scenarios = _stake_scenarios(trades)
    agg = _research_aggregate()

    # Proposed sizing preview on closed trades
    try:
        from polymarket_wallet import cached_wallet_cash
        wallet = float(cached_wallet_cash() or 500)
    except Exception:
        wallet = 500.0
    proposed_net = 0.0
    for r in closed:
        ask = float(r.get("entry_ask") or 0.5)
        asset = str(r.get("asset") or "BTC").upper()
        tier = str(r.get("tier") or "favorite")
        period = str(r.get("period") or "outside_us")
        stake = stake_for_proposed(asset, tier, period, wallet=wallet)
        won = r.get("trade_result") == "WIN"
        proposed_net += _simulate_pnl(stake, ask, won)
    proposed_net = round(proposed_net, 2)

    current_net = round(
        sum(float(r.get("pnl") or 0) for r in closed)
        - sum(_est_fee(float(r.get("stake") or 0), float(r.get("entry_ask") or 0.5)) for r in closed),
        2,
    )

    recommendations: list[dict[str, Any]] = []
    for c in cells:
        rec = "hold"
        note = ""
        if c["trades"] >= 3 and (c["ev_net_per_trade"] or 0) < -5:
            rec = "reduce_or_disable"
            note = "3+ trades, net EV negative"
        elif c["trades"] >= 5 and (c["ev_net_per_trade"] or 0) > 8:
            rec = "consider_increase"
            note = "strong net EV"
        elif c["trades"] == 1:
            rec = "need_more_data"
            note = "n=1 only"
        elif c["trades"] < 3:
            rec = "need_more_data"
            note = f"n={c['trades']}"
        if c["tier"] == "underdog" and c["trades"] < 3:
            rec = "need_more_data"
            note = "underdog — wait for samples before live size"
        recommendations.append({**c, "recommendation": rec, "note": note})

    inactive_cells = []
    for cell in sorted(ALPHA_CELLS):
        key = f"{cell[0]}|{cell[1]}|{cell[2]}|{cell[3]:02d}"
        if key not in {c["cell"] for c in cells}:
            inactive_cells.append(key)

    report = {
        "generated_at_hkt": now_hkt_str(),
        "paper_session": _load_json(V1_LOG_DIR / "alpha_max_session.json", {}),
        "summary": _closed_summary(trades),
        "research_aggregate": agg,
        "by_cell": cells,
        "inactive_cells": inactive_cells[:40],
        "inactive_cells_count": len(inactive_cells),
        "stake_scenarios": scenarios,
        "sizing": {
            "current_rules": STAKE_RULES,
            "current_net_pnl_est": current_net,
            "proposed_wallet_cap_pct": PROPOSED_WALLET_CAP_PCT,
            "proposed_core_wallet_base": PROPOSED_CORE_WALLET_MULT_BASE,
            "proposed_net_pnl_est": proposed_net,
            "proposed_note": (
                f"Cap each trade at {PROPOSED_WALLET_CAP_PCT:.0%} wallet; "
                f"core scales with wallet/{PROPOSED_CORE_WALLET_MULT_BASE:.0f}; "
                f"underdog max 8% wallet"
            ),
            "wallet_used_for_proposal": round(wallet, 2),
        },
        "go_live": _go_live_readiness(),
    }
    if refresh_cache:
        RESEARCH_CACHE.parent.mkdir(parents=True, exist_ok=True)
        RESEARCH_CACHE.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report


def _closed_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if r.get("trade_result") in ("WIN", "LOSS")]
    wins = sum(1 for r in closed if r.get("trade_result") == "WIN")
    gross = round(sum(float(r.get("pnl") or 0) for r in closed), 2)
    fees = round(sum(_est_fee(float(r.get("stake") or 0), float(r.get("entry_ask") or 0.5)) for r in closed), 2)
    return {
        "trades_closed": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "win_rate": round(100 * wins / len(closed), 1) if closed else None,
        "gross_pnl": gross,
        "est_fees": fees,
        "net_pnl_est": round(gross - fees, 2),
    }


def backfill_research_from_windows(*, max_windows: int = 2000) -> int:
    """Backfill research jsonl from main observer window caches."""
    written = 0
    seen_slugs: set[str] = set()
    if RESEARCH_FILE.exists():
        for line in RESEARCH_FILE.read_text().splitlines():
            if not line.strip():
                continue
            try:
                seen_slugs.add(json.loads(line).get("slug", ""))
            except json.JSONDecodeError:
                pass

    sources = [
        (V1_LOG_DIR / "windows.json", "BTC"),
        (V1_LOG_DIR / "eth_windows.json", "ETH"),
    ]
    for path, asset in sources:
        windows = _load_json(path, [])
        if not isinstance(windows, list):
            continue
        for rec in windows[-max_windows:]:
            slug = rec.get("slug") or ""
            if not slug or slug in seen_slugs:
                continue
            cps = rec.get("checkpoints") or []
            outcome = rec.get("outcome") or rec.get("outcome_estimate")
            if not cps or outcome not in ("up", "down"):
                continue
            record_window_evaluation(
                asset=asset,
                slug=slug,
                title=rec.get("title") or "",
                checkpoints=cps,
                outcome=outcome,
                source="backfill",
            )
            seen_slugs.add(slug)
            written += 1
    return written
