"""Polymarket wallet withdrawals/deposits — excluded from bot/manual PnL, used for cash reconciliation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FLOWS_FILE = ROOT / "logs" / "wallet_flows.json"
HKT = timezone(timedelta(hours=8))


def _today_hkt() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d")


def _session_baseline_hkt() -> str | None:
    try:
        from manual_bets import session_baseline_hkt

        return session_baseline_hkt()
    except Exception:
        pass
    path = ROOT / "logs" / "live_session.json"
    if path.exists():
        try:
            started = str(json.loads(path.read_text()).get("started_at_hkt") or "")[:19]
            return started or None
        except (json.JSONDecodeError, OSError):
            pass
    return None


def load_wallet_flows() -> dict[str, Any]:
    if not FLOWS_FILE.exists():
        return {"version": 1, "flows": [], "note": "Withdrawals/deposits outside bot & manual PnL."}
    try:
        data = json.loads(FLOWS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "flows": []}
    if not isinstance(data, dict):
        return {"version": 1, "flows": list(data or [])}
    return data


def save_wallet_flows(data: dict[str, Any]) -> None:
    FLOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_hkt"] = datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")
    FLOWS_FILE.write_text(json.dumps(data, indent=2) + "\n")


def session_flows(flows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = flows if flows is not None else list(load_wallet_flows().get("flows") or [])
    baseline = _session_baseline_hkt()
    if not baseline:
        return rows
    return [f for f in rows if str(f.get("timestamp_hkt") or "")[:19] >= baseline]


def _sum_flows(rows: list[dict[str, Any]], kind: str) -> float:
    total = 0.0
    for row in rows:
        if str(row.get("kind") or "").lower() != kind:
            continue
        if row.get("inferred"):
            continue
        total += float(row.get("amount_usd") or 0)
    return round(total, 2)


def wallet_flows_summary(
    *,
    account_pnl: float | None = None,
    bots_log_sum: float = 0.0,
    manual_session_pnl: float = 0.0,
    starting_wallet_usdc: float = 0.0,
    wallet_cash: float | None = None,
) -> dict[str, Any]:
    """Summarize explicit + inferred withdrawals/deposits for the current session."""
    rows = session_flows()
    withdrawals_explicit = _sum_flows(rows, "withdrawal")
    deposits_explicit = _sum_flows(rows, "deposit")

    gross_trading_pnl = round(bots_log_sum + manual_session_pnl, 2)
    inferred_withdrawal = 0.0
    inferred_deposit = 0.0
    reconcile_after_flows: float | None = None

    if account_pnl is not None:
        # cash change = trading pnl - net withdrawals (+ deposits)
        net_outflow = round(gross_trading_pnl - account_pnl, 2)
        explicit_net_out = round(withdrawals_explicit - deposits_explicit, 2)
        remainder = round(net_outflow - explicit_net_out, 2)
        if remainder > 1.0:
            inferred_withdrawal = remainder
        elif remainder < -1.0:
            inferred_deposit = round(-remainder, 2)

    withdrawals_total = round(withdrawals_explicit + inferred_withdrawal, 2)
    deposits_total = round(deposits_explicit + inferred_deposit, 2)
    net_flows = round(deposits_total - withdrawals_total, 2)

    expected_account_pnl = round(gross_trading_pnl + net_flows, 2)
    if account_pnl is not None:
        reconcile_after_flows = round(account_pnl - expected_account_pnl, 2)

    expected_wallet = None
    if wallet_cash is not None and starting_wallet_usdc > 0:
        expected_wallet = round(starting_wallet_usdc + expected_account_pnl, 2)
        if wallet_cash is not None:
            reconcile_after_flows = round(wallet_cash - expected_wallet, 2)

    return {
        "session_baseline_hkt": _session_baseline_hkt(),
        "flows_count": len(rows),
        "withdrawals_explicit_usd": withdrawals_explicit,
        "deposits_explicit_usd": deposits_explicit,
        "withdrawals_inferred_usd": inferred_withdrawal,
        "deposits_inferred_usd": inferred_deposit,
        "withdrawals_total_usd": withdrawals_total,
        "deposits_total_usd": deposits_total,
        "net_flows_usd": net_flows,
        "gross_trading_pnl_usd": gross_trading_pnl,
        "expected_account_pnl_usd": expected_account_pnl,
        "reconcile_after_flows_usd": reconcile_after_flows,
        "expected_wallet_usd": expected_wallet,
        "flows": rows,
        "formula": (
            "現金 ≈ 起始 + Bot + Manual − 提款 + 入金 · "
            "推估提款 = (Bot+Manual) − 帳戶PnL − 已記錄提款 + 已記錄入金"
        ),
    }


def add_flow(
    *,
    kind: str,
    amount_usd: float,
    timestamp_hkt: str | None = None,
    note: str = "",
    source: str = "user",
) -> dict[str, Any]:
    kind = kind.lower()
    if kind not in ("withdrawal", "deposit"):
        raise ValueError("kind must be withdrawal or deposit")
    data = load_wallet_flows()
    flows = list(data.get("flows") or [])
    ts = timestamp_hkt or datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "id": f"{kind}-{len(flows)+1}-{ts[:10]}",
        "kind": kind,
        "amount_usd": round(float(amount_usd), 2),
        "timestamp_hkt": ts,
        "note": note,
        "source": source,
        "inferred": False,
    }
    flows.append(entry)
    data["flows"] = flows
    save_wallet_flows(data)
    return entry
