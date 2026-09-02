"""Strategy overview — config, live PnL by tier, capital scaling ($1000 base)."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path("/root/btc-5m-bot")
CONFIG_FILE = ROOT / "logs" / "strategy_config.json"
BTC_LOG = ROOT / "logs" / "trade_log_live.json"
ETH_LOG = ROOT / "logs" / "trade_log_eth_live.json"
EXT_FILE = ROOT / "logs" / "extended_hours_live.json"

ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
BASE_STAKES = {
    "btc": {"fav": 18.0, "dog": 30.0, "fav_core": 27.0, "dog_core": 45.0},
    "eth": {"fav": 24.0, "dog": 30.0, "fav_core": 36.0, "dog_core": 45.0},
}
CAPITAL_BASE_USD = 1000.0
RECOMMENDED_MULT = 3.0
RECOMMENDED_MODE = "all"


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _parse_hkt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(str(ts).replace(" HKT", "")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=HKT)
    except ValueError:
        return None


def _in_us_weekday(dt_et: datetime) -> bool:
    if dt_et.weekday() >= 5:
        return False
    m = dt_et.hour * 60 + dt_et.minute
    return 9 * 60 + 30 <= m < 16 * 60


def _in_core(dt_et: datetime) -> bool:
    if not _in_us_weekday(dt_et):
        return False
    m = dt_et.hour * 60 + dt_et.minute
    return 11 * 60 <= m < 15 * 60


def _period_match(period: str, ts: str | None) -> bool:
    dt = _parse_hkt(ts)
    if dt is None:
        return False
    et = dt.astimezone(ET)
    if period == "all":
        return True
    if period == "us_hours":
        return _in_us_weekday(et)
    if period == "core":
        return _in_core(et)
    if period == "outside_us":
        return not _in_us_weekday(et)
    return True


def _closed_rows(path: Path, *, exclude_stake_ge: float | None = None) -> list[dict[str, Any]]:
    rows = _load_json(path, [])
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if not r.get("entered") or r.get("trade_result") not in ("WIN", "LOSS"):
            continue
        if exclude_stake_ge is not None and float(r.get("stake") or 0) >= exclude_stake_ge:
            continue
        out.append(r)
    return out


def _tier_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "wins": 0, "pnl": 0.0, "stakes": defaultdict(lambda: {"n": 0, "pnl": 0.0})}
    )
    for r in rows:
        tier = str(r.get("tier") or "?").lower()
        if tier == "mid":
            continue
        key = "fav" if tier == "favorite" else ("dog" if tier == "underdog" else tier)
        stake = round(float(r.get("stake") or 0), 1)
        pnl = float(r.get("pnl") or 0)
        by[key]["n"] += 1
        by[key]["wins"] += 1 if r.get("trade_result") == "WIN" else 0
        by[key]["pnl"] += pnl
        by[key]["stakes"][stake]["n"] += 1
        by[key]["stakes"][stake]["pnl"] += pnl
    result = {}
    for k, v in by.items():
        n = v["n"]
        stakes = {
            str(sk): {"n": sv["n"], "pnl": round(sv["pnl"], 2)}
            for sk, sv in sorted(v["stakes"].items())
        }
        result[k] = {
            "n": n,
            "wins": v["wins"],
            "losses": n - v["wins"],
            "wr_pct": round(v["wins"] / n * 100, 1) if n else 0,
            "pnl": round(v["pnl"], 2),
            "stakes": stakes,
        }
    return result


def _period_stats(btc_rows: list[dict], eth_rows: list[dict], period: str) -> dict[str, Any]:
    b = [r for r in btc_rows if _period_match(period, r.get("timestamp_hkt"))]
    e = [r for r in eth_rows if _period_match(period, r.get("timestamp_hkt"))]
    if not b and not e:
        return {"period": period, "n": 0}
    all_rows = b + e
    days = len({str(r.get("timestamp_hkt", ""))[:10] for r in all_rows}) or 1
    pnl = sum(float(r.get("pnl") or 0) for r in all_rows)
    wins = sum(1 for r in all_rows if r.get("trade_result") == "WIN")
    return {
        "period": period,
        "n": len(all_rows),
        "btc_n": len(b),
        "eth_n": len(e),
        "wr_pct": round(wins / len(all_rows) * 100, 1),
        "pnl": round(pnl, 2),
        "daily_pnl": round(pnl / days, 2),
        "days": days,
        "btc_pnl": round(sum(float(r.get("pnl") or 0) for r in b), 2),
        "eth_pnl": round(sum(float(r.get("pnl") or 0) for r in e), 2),
    }


def _strategy_row(
    *,
    asset: str,
    cfg: dict[str, Any],
    p2: dict[str, Any],
    ext: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    cm = float(cfg.get("core_stake_multiplier") or 1.5)
    wm = 1.0
    if p2.get("core_wallet_scaling"):
        base = float(p2.get("core_wallet_base_usd") or 1000.0)
        try:
            from polymarket_wallet import cached_wallet_cash

            wallet = float(cached_wallet_cash() or 0)
        except Exception:
            wallet = 0.0
        if base > 0 and wallet > 0:
            wm = max(1.0, wallet / base)
    fav = float(cfg.get("stake_favorite") or p2.get("stake_favorite", 18))
    dog = float(cfg.get("stake_underdog") or p2.get("stake_underdog", 30))
    pause = int(cfg.get("favorite_loss_streak_pause") or p2.get("favorite_loss_streak_pause", 2))
    pause_h = float(cfg.get("tier_pause_hours") or p2.get("tier_pause_hours", 2))
    daily_lim = float(cfg.get("daily_loss_limit_usd") or p2.get("daily_loss_limit_usd", 75))
    us_start = p2.get("us_regular_start_et", "09:30")
    us_end = p2.get("us_regular_end_et", "16:00")
    core_start = p2.get("core_window_start_et", "11:00")
    core_end = p2.get("core_window_end_et", "15:00")

    if asset == "btc":
        ext_on = bool(ext.get("btc_enabled"))
        ext_mode = "extended_dog_only" if ext.get("btc_extended_dog_only") else ("extended" if ext_on else "us_hours")
    else:
        ext_on = bool(ext.get("eth_enabled"))
        if ext_on and cfg.get("favorite_us_only"):
            ext_mode = "extended · fav US-only · dog 24h"
        else:
            ext_mode = "extended" if ext_on else "us_hours"

    return {
        "asset": asset.upper(),
        "package": "Package 2+",
        "trading_period": f"Mon–Fri {us_start}–{us_end} ET (US)",
        "core_window": f"Mon–Fri {core_start}–{core_end} ET ×{cm:g} × wallet {wm:.2f}",
        "extended_mode": ext_mode,
        "extended_live": ext_on,
        "stakes": {
            "fav": fav,
            "fav_core": round(fav * cm * wm, 2),
            "dog": float(cfg.get("stake_underdog") or dog),
            "dog_core": round(float(cfg.get("stake_underdog") or dog) * cm * wm, 2),
        },
        "pause": f"{pause} consecutive losses → pause {pause_h:g}h (per tier)",
        "daily_loss_limit": daily_lim,
        "max_ask_fav": p2.get("max_ask_favorite"),
        "entry_timing": "T75–T22 (BTC) / ETH aligned",
    }


def _hybrid_plan_a_row(base_daily_all: float) -> dict[str, Any]:
    """BTC 3× + ETH fav 2× + ETH dog 3× — matches live config."""
    b, e = BASE_STAKES["btc"], BASE_STAKES["eth"]
    btc_m, eth_fav_m, eth_dog_m = 3.0, 2.0, 3.0
    daily = round(base_daily_all * 3.0, 2)
    return {
        "mult": 3.0,
        "plan": "A_hybrid",
        "capital_base": CAPITAL_BASE_USD,
        "btc_fav": round(b["fav"] * btc_m),
        "btc_fav_core": round(b["fav_core"] * btc_m),
        "btc_dog": round(b["dog"] * btc_m),
        "btc_dog_core": round(b["dog_core"] * btc_m),
        "eth_fav": round(e["fav"] * eth_fav_m),
        "eth_fav_core": round(e["fav_core"] * eth_fav_m),
        "eth_dog": round(e["dog"] * eth_dog_m),
        "eth_dog_core": round(e["dog_core"] * eth_dog_m),
        "daily_loss_btc": round(BASE_DAILY_LIMITS["btc"] * btc_m),
        "daily_loss_eth": round(BASE_DAILY_LIMITS["eth"] * btc_m),
        "est_daily_pnl": daily,
        "est_month_pnl": round(daily * 22, 0),
        "recommended": True,
        "note": "BTC 3× ext full · ETH fav 2× US · ETH dog 3× ext",
    }


def _scaling_row(mult: float, base_daily: float, btc_stakes: dict, eth_stakes: dict, btc_lim: float, eth_lim: float) -> dict[str, Any]:
    daily = round(base_daily * mult, 2)
    return {
        "mult": mult,
        "capital_base": CAPITAL_BASE_USD,
        "btc_fav": round(btc_stakes["fav"] * mult),
        "btc_fav_core": round(btc_stakes["fav_core"] * mult),
        "btc_dog": round(btc_stakes["dog"] * mult),
        "btc_dog_core": round(btc_stakes["dog_core"] * mult),
        "eth_fav": round(eth_stakes["fav"] * mult),
        "eth_fav_core": round(eth_stakes["fav_core"] * mult),
        "eth_dog": round(eth_stakes["dog"] * mult),
        "eth_dog_core": round(eth_stakes["dog_core"] * mult),
        "daily_loss_btc": round(btc_lim * mult),
        "daily_loss_eth": round(eth_lim * mult),
        "est_daily_pnl": daily,
        "est_month_pnl": round(daily * 22, 0),
        "recommended": mult == RECOMMENDED_MULT,
    }


def build_strategy_overview() -> dict[str, Any]:
    cfg_root = _load_json(CONFIG_FILE, {})
    p2 = cfg_root.get("package2") or {}
    eth_cfg = cfg_root.get("eth_package2") or {}
    ext = _load_json(EXT_FILE, {})

    btc_rows = _closed_rows(BTC_LOG)
    eth_rows_clean = _closed_rows(ETH_LOG, exclude_stake_ge=100)
    eth_rows_all = _closed_rows(ETH_LOG)

    strategy_table = [
        _strategy_row(asset="btc", cfg=p2, p2=p2, ext=ext, mode="btc"),
        _strategy_row(asset="eth", cfg=eth_cfg, p2=p2, ext=ext, mode="eth"),
    ]

    pnl_tiers = {
        "btc": _tier_stats(btc_rows),
        "eth_clean": _tier_stats(eth_rows_clean),
        "eth_all": _tier_stats(eth_rows_all),
    }

    mode_comparison = [
        _period_stats(btc_rows, eth_rows_clean, "us_hours"),
        _period_stats(btc_rows, eth_rows_clean, "core"),
        _period_stats(btc_rows, eth_rows_clean, "outside_us"),
        _period_stats(btc_rows, eth_rows_all, "all"),
    ]
    for m in mode_comparison:
        m["label"] = {
            "us_hours": "US 時段 only (9:30–16 ET)",
            "core": "Core only (11–15 ET)",
            "outside_us": "Extended / 非 US 時段",
            "all": "全部時段 (現況 extended ON)",
        }.get(m.get("period", ""), m.get("period"))

    us_daily = next((m["daily_pnl"] for m in mode_comparison if m.get("period") == "all"), 67.0)

    btc_base = BASE_STAKES["btc"]
    eth_base = BASE_STAKES["eth"]
    capital_plan = [
        _scaling_row(m, us_daily, btc_base, eth_base, BASE_DAILY_LIMITS["btc"], BASE_DAILY_LIMITS["eth"])
        for m in (2.0, 2.5, 4.0)
    ]
    capital_plan.append(_hybrid_plan_a_row(us_daily))
    capital_plan.sort(key=lambda r: r["mult"])

    bt_raw = _load_json(ROOT / "logs" / "backtest_package2_maxask.json", {})
    bt_top = {}
    if isinstance(bt_raw.get("grid_full_top10"), list) and bt_raw["grid_full_top10"]:
        bt_top = bt_raw["grid_full_top10"][0]
    elif isinstance(bt_raw.get("top"), list) and bt_raw["top"]:
        bt_top = bt_raw["top"][0]

    return {
        "capital_base_usd": CAPITAL_BASE_USD,
        "recommended_mult": RECOMMENDED_MULT,
        "recommended_mode": RECOMMENDED_MODE,
        "recommended_mode_label": "Plan A hybrid · BTC 3× extended full · ETH fav 2× US-only · ETH dog 3× extended",
        "strategy_table": strategy_table,
        "pnl_by_tier": pnl_tiers,
        "mode_comparison": mode_comparison,
        "capital_plan": capital_plan,
        "applied_plan": cfg_root.get("capital_plan"),
        "backtest_btc": {
            "scenario": bt_top.get("key") or bt_top.get("scenario") or "T70-22 · fav≤0.93",
            "trades": bt_top.get("n") or bt_top.get("trades"),
            "wr_pct": bt_top.get("wr") or bt_top.get("wr_pct"),
            "pnl": bt_top.get("pnl"),
            "note": f"{bt_raw.get('dataset', {}).get('with_checkpoints', 480)} BTC windows · start ${bt_raw.get('start_bankroll', 102)}",
        },
        "generated_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S"),
    }
