"""Package 2+ — OLD-base three-tier fixed stakes (favorite $7 / mid $5 / underdog $10)."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from bot_common import ETH_LIVE_TRADE_LOG_FILE, LIVE_TRADE_LOG_FILE, LOG_DIR, STRATEGY_CONFIG_FILE
from new_strategy import (
    bought_label,
    calc_loss_pnl,
    calc_win_pnl,
    load_config,
    side_to_contract,
    trade_result,
)

HKT = ZoneInfo("Asia/Hong_Kong")
ET = ZoneInfo("America/New_York")
RISK_STATE_FILE = LOG_DIR / "package2_risk_state.json"
EXTENDED_HOURS_LIVE_FILE = LOG_DIR / "extended_hours_live.json"
EXTENDED_HOURS_DISABLED = True

# Recommended wide window: pause BTC/ETH underdog only (ET calendar).
WEEKEND_DOG_PAUSE = {
    "enabled": True,
    "pending": False,
    "label": "Recommended · Dog only",
    "et_window": "Fri 16:00 – Mon 09:30 ET",
    "hkt_window": "Sat 04:00 – Mon 21:30 HKT",
    "tiers": ["underdog"],
    "assets": ["btc", "eth"],
}


def in_weekend_dog_pause_window(now: datetime | None = None) -> bool:
    if not WEEKEND_DOG_PAUSE.get("enabled"):
        return False
    now_et = now or datetime.now(ET)
    if now_et.tzinfo is None:
        now_et = now_et.replace(tzinfo=ET)
    else:
        now_et = now_et.astimezone(ET)
    wd = now_et.weekday()
    t = now_et.time()
    if wd == 4 and t >= time(16, 0):
        return True
    if wd in (5, 6):
        return True
    if wd == 0 and t < time(9, 30):
        return True
    return False


def weekend_dog_pause_skip_reason() -> str:
    w = WEEKEND_DOG_PAUSE
    return (
        f"Skipped: weekend dog pause ({w['et_window']} / {w['hkt_window']}) — "
        "underdog blocked; favorite rules unchanged"
    )

DEFAULTS = {
    "max_ask": 0.88,
    "max_ask_favorite": 0.90,
    "max_ask_underdog": 0.88,
    "min_ask": 0.15,
    "favorite_ask": 0.70,
    "mid_low_ask": 0.30,
    "stake_favorite": 7.0,
    "stake_mid": 5.0,
    "stake_underdog": 10.0,
    "underdog_min_depth": 200.0,
    "underdog_min_delta_pct": 0.035,
    "underdog_min_move_usd": 25.0,
    "underdog_t_min": 30,
    "underdog_t_max": 60,
    "daily_loss_limit_usd": 25.0,
    "tier_pause_hours": 2.0,
    "favorite_loss_streak_pause": 2,
    "underdog_loss_streak_pause": 2,
    "underdog_pause_hours": 2,
    "underdog_pause_minutes": 0,
    "favorite_pause_minutes": 0,
    "favorite_pause_hours": 2,
    "favorite_only": False,
    "skip_mid": False,
    "us_regular_only": False,
    "us_weekdays_only": True,
    "us_regular_start_et": "09:30",
    "us_regular_end_et": "16:00",
    "core_window_start_et": "11:00",
    "core_window_end_et": "15:00",
    "core_stake_multiplier": 1.5,
    "underdog_only": False,
    "underdog_extra_filters": False,
    "favorite_us_only": False,
    "favorite_core_only": False,
    "core_wallet_scaling": False,
    "core_wallet_base_usd": 1000.0,
    "btc_favorite_skip_hours_et": [],
}


def package2_config(asset: str = "btc") -> dict[str, Any]:
    cfg = load_config()
    p2 = cfg.get("package2") or {}
    out = dict(DEFAULTS)
    out.update({k: p2[k] for k in p2 if k in out})
    if asset.lower() == "eth":
        eth = cfg.get("eth_package2") or {}
        for k in eth:
            if k in out:
                out[k] = eth[k]
        if "core_stake_multiplier" in eth:
            out["core_stake_multiplier"] = float(eth["core_stake_multiplier"])
    # BTC-only: package2.favorite_only disables BTC underdog, not ETH.
    if asset.lower() == "eth":
        eth_cfg = cfg.get("eth_package2") or {}
        out["favorite_only"] = bool(eth_cfg.get("favorite_only", False))
    elif "favorite_only" in p2:
        out["favorite_only"] = bool(p2["favorite_only"])
    if "skip_mid" in p2:
        out["skip_mid"] = bool(p2["skip_mid"])
    if "us_regular_only" in p2:
        out["us_regular_only"] = bool(p2["us_regular_only"])
    if "us_weekdays_only" in p2:
        out["us_weekdays_only"] = bool(p2["us_weekdays_only"])
    if "us_regular_start_et" in p2:
        out["us_regular_start_et"] = str(p2["us_regular_start_et"])
    if "us_regular_end_et" in p2:
        out["us_regular_end_et"] = str(p2["us_regular_end_et"])
    if "core_window_start_et" in p2:
        out["core_window_start_et"] = str(p2["core_window_start_et"])
    if "core_window_end_et" in p2:
        out["core_window_end_et"] = str(p2["core_window_end_et"])
    if "core_stake_multiplier" in p2:
        out["core_stake_multiplier"] = float(p2["core_stake_multiplier"])
    if "underdog_only" in p2:
        out["underdog_only"] = bool(p2["underdog_only"])
    if "underdog_extra_filters" in p2:
        out["underdog_extra_filters"] = bool(p2["underdog_extra_filters"])
    if "favorite_pause_minutes" in p2:
        out["favorite_pause_minutes"] = int(p2["favorite_pause_minutes"])
    if "underdog_loss_streak_pause" in p2:
        out["underdog_loss_streak_pause"] = int(p2["underdog_loss_streak_pause"])
    if "underdog_pause_hours" in p2:
        out["underdog_pause_hours"] = int(p2["underdog_pause_hours"])
    if "tier_pause_hours" in p2:
        out["tier_pause_hours"] = float(p2["tier_pause_hours"])
    if "underdog_pause_minutes" in p2:
        out["underdog_pause_minutes"] = int(p2["underdog_pause_minutes"])
    if "favorite_pause_hours" in p2:
        out["favorite_pause_hours"] = int(p2["favorite_pause_hours"])
    if "core_wallet_scaling" in p2:
        out["core_wallet_scaling"] = bool(p2["core_wallet_scaling"])
    if "core_wallet_base_usd" in p2:
        out["core_wallet_base_usd"] = float(p2["core_wallet_base_usd"])
    if "btc_favorite_skip_hours_et" in p2:
        out["btc_favorite_skip_hours_et"] = [
            int(x) for x in (p2.get("btc_favorite_skip_hours_et") or [])
        ]
    return out


def is_package2_active() -> bool:
    cfg = load_config()
    return cfg.get("current_strategy", "").upper() in ("PACKAGE2+", "PACKAGE2")


def _normalize_risk_state(state: dict) -> dict:
    out: dict[str, Any] = {
        "v1_global": dict(state.get("v1_global") or _default_v1_global()),
        "btc": dict(state.get("btc") or {}),
        "eth": dict(state.get("eth") or {}),
    }
    if "favorite_loss_streak" in state and not out["btc"].get("favorite_loss_streak"):
        out["btc"]["favorite_loss_streak"] = int(state.get("favorite_loss_streak", 0))
        out["btc"]["favorite_paused_until_utc"] = state.get("favorite_paused_until_utc")
    if "underdog_loss_streak" in state and not out["btc"].get("underdog_loss_streak"):
        out["btc"]["underdog_loss_streak"] = int(state.get("underdog_loss_streak", 0))
        out["btc"]["underdog_paused_until_utc"] = state.get("underdog_paused_until_utc")
    for asset in ("btc", "eth"):
        block = out[asset]
        for tier in ("favorite", "underdog"):
            block.setdefault(f"{tier}_loss_streak", 0)
            block.setdefault(f"{tier}_paused_until_utc", None)
        out[asset] = block
    return out


def _tier_pause_until(cfg: dict[str, Any], tier: str) -> datetime:
    now = datetime.now(timezone.utc)
    unified = cfg.get("tier_pause_hours")
    if unified:
        return now + timedelta(hours=float(unified))
    if tier == "favorite":
        if cfg.get("favorite_pause_hours"):
            return now + timedelta(hours=int(cfg["favorite_pause_hours"]))
        return now + timedelta(minutes=int(cfg.get("favorite_pause_minutes", 120)))
    if cfg.get("underdog_pause_minutes"):
        return now + timedelta(minutes=int(cfg["underdog_pause_minutes"]))
    return now + timedelta(hours=int(cfg.get("underdog_pause_hours", 2)))


def _tier_pause_label(cfg: dict[str, Any], tier: str) -> str:
    if cfg.get("tier_pause_hours"):
        h = float(cfg["tier_pause_hours"])
        return f"{int(h)}h" if h == int(h) else f"{h}h"
    if tier == "favorite":
        if cfg.get("favorite_pause_hours"):
            return f"{int(cfg['favorite_pause_hours'])}h"
        return f"{int(cfg.get('favorite_pause_minutes', 120))}m"
    if cfg.get("underdog_pause_minutes"):
        return f"{int(cfg['underdog_pause_minutes'])}m"
    return f"{int(cfg.get('underdog_pause_hours', 2))}h"


def _load_risk_state() -> dict:
    if RISK_STATE_FILE.exists():
        try:
            return _normalize_risk_state(json.loads(RISK_STATE_FILE.read_text()))
        except json.JSONDecodeError:
            pass
    return _normalize_risk_state({})


def _pause_until_from_cfg(cfg: dict[str, Any], *, mode_key: str) -> datetime:
    mode = str(cfg.get(mode_key, "hkt_day")).lower()
    if mode == "hours":
        hours = int(cfg.get("global_pause_hours", 4))
        return datetime.now(timezone.utc) + timedelta(hours=hours)
    now_hkt = datetime.now(HKT)
    midnight = (now_hkt + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight.astimezone(timezone.utc)


def _paused_until_active(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        until = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < until:
            return until
    except ValueError:
        pass
    return None


def streak_pause_skip_reason(kind: str, *, asset: str = "btc") -> str:
    vr = v1_risk_config()
    label = asset.upper()
    if kind == "favorite":
        n = int(vr.get("favorite_loss_streak_pause", 2))
        return (
            f"Skipped: V1 favorite stopped "
            f"({n} consecutive fav losses across BTC+ETH · manual resume required)"
        )
    n = int(vr.get("underdog_loss_streak_pause", 2))
    return (
        f"Skipped: V1 underdog stopped "
        f"({n} consecutive dog losses across BTC+ETH · manual resume required)"
    )


def risk_pause_status(*, asset: str = "btc") -> dict[str, Any]:
    vr = v1_risk_config()
    state = _load_risk_state()
    global_block = state.get("v1_global") or _default_v1_global()
    return {
        "asset": asset.lower(),
        "favorite_paused": bool(global_block.get("favorite_stopped")),
        "favorite_paused_seconds_left": None,
        "favorite_paused_until_utc": None,
        "underdog_paused": bool(global_block.get("underdog_stopped")),
        "underdog_paused_seconds_left": None,
        "underdog_paused_until_utc": None,
        "favorite_loss_streak": int(global_block.get("favorite_loss_streak", 0)),
        "underdog_loss_streak": int(global_block.get("underdog_loss_streak", 0)),
        "v1_combined_daily_pnl": v1_combined_daily_pnl(),
        "v1_combined_daily_loss_limit_usd": v1_combined_daily_loss_limit_usd(),
        "v1_combined_daily_loss_limit_hit": v1_combined_daily_loss_limit_hit(),
        "rules": {
            "cross_asset_tier_streak": bool(vr.get("cross_asset_tier_streak", True)),
            "same_tier_only": True,
            "favorite": {
                "losses": int(vr.get("favorite_loss_streak_pause", 2)),
                "pause": "manual until cleared",
            },
            "underdog": {
                "losses": int(vr.get("underdog_loss_streak_pause", 2)),
                "pause": "manual until cleared",
            },
            "combined_daily_loss_limit_usd": v1_combined_daily_loss_limit_usd(),
        },
    }


def _save_risk_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RISK_STATE_FILE.write_text(json.dumps(state, indent=2))


def record_closed_trade(*, tier: str, won: bool, asset: str = "btc") -> None:
    """Cross-asset V1 streak: BTC fav loss + ETH fav loss counts toward fav stop."""
    if tier not in ("favorite", "underdog"):
        return
    state = _load_risk_state()
    vr = v1_risk_config()
    block = dict(state.get("v1_global") or _default_v1_global())
    streak_key = f"{tier}_loss_streak"
    stopped_key = f"{tier}_stopped"
    threshold = int(
        vr["favorite_loss_streak_pause"]
        if tier == "favorite"
        else vr["underdog_loss_streak_pause"]
    )
    if won:
        block[streak_key] = 0
    else:
        block[streak_key] = int(block.get(streak_key) or 0) + 1
        if block[streak_key] >= threshold:
            block[stopped_key] = True
            block[streak_key] = 0
            from bot_common import setup_logger

            setup_logger().warning(
                "V1 %s stopped after %d consecutive cross-asset losses (last=%s)",
                tier,
                threshold,
                asset.upper(),
            )
    state["v1_global"] = block
    _save_risk_state(state)
    enforce_v1_combined_loss_stop()


def global_paused() -> bool:
    """Deprecated — global cross-tier pause removed."""
    return False


def favorite_paused(*, asset: str = "btc") -> bool:
    state = _load_risk_state()
    block = state.get("v1_global") or _default_v1_global()
    return bool(block.get("favorite_stopped"))


def underdog_paused(*, asset: str = "btc") -> bool:
    state = _load_risk_state()
    block = state.get("v1_global") or _default_v1_global()
    return bool(block.get("underdog_stopped"))


def clear_tier_pause(*, asset: str, tier: str = "all") -> dict[str, Any]:
    """Manually clear V1 cross-asset tier stop (dashboard resume button)."""
    tier = tier.lower()
    if tier not in ("favorite", "underdog", "all"):
        raise ValueError("tier must be favorite, underdog, or all")
    state = _load_risk_state()
    block = dict(state.get("v1_global") or _default_v1_global())
    if tier in ("favorite", "all"):
        block["favorite_stopped"] = False
        block["favorite_loss_streak"] = 0
    if tier in ("underdog", "all"):
        block["underdog_stopped"] = False
        block["underdog_loss_streak"] = 0
    state["v1_global"] = block
    _save_risk_state(state)
    return risk_pause_status(asset=asset)


def in_core_window(now: datetime | None = None, *, asset: str = "btc") -> bool:
    """Mon–Fri core window (default 11:00–15:00 ET)."""
    cfg = package2_config(asset)
    now = (now or datetime.now(ET)).astimezone(ET)
    if cfg.get("us_weekdays_only", True) and now.weekday() >= 5:
        return False
    start_h, start_m = _parse_hm(str(cfg.get("core_window_start_et", "11:00")))
    end_h, end_m = _parse_hm(str(cfg.get("core_window_end_et", "15:00")))
    cur = now.hour * 60 + now.minute
    return (start_h * 60 + start_m) <= cur < (end_h * 60 + end_m)


def core_stake_multiplier(asset: str = "btc", now: datetime | None = None) -> float:
    """Mon–Fri 11:00–15:00 ET × core_stake_multiplier when > 1 (BTC + ETH)."""
    asset = asset.lower()
    if asset not in ("btc", "eth"):
        return 1.0
    cfg = package2_config(asset)
    mult = float(cfg.get("core_stake_multiplier") or 1.0)
    if mult <= 1.0:
        return 1.0
    if not in_us_regular_session(now):
        return 1.0
    if not in_core_window(now, asset=asset):
        return 1.0
    return mult


def core_wallet_multiplier(asset: str = "btc", now: datetime | None = None) -> float:
    """Scale core-window stakes by wallet size; non-core remains fixed 1x."""
    cfg = package2_config(asset)
    if not cfg.get("core_wallet_scaling"):
        return 1.0
    if not in_us_regular_session(now) or not in_core_window(now, asset=asset):
        return 1.0
    base = float(cfg.get("core_wallet_base_usd") or 1000.0)
    if base <= 0:
        return 1.0
    try:
        from polymarket_wallet import cached_wallet_cash

        wallet = float(cached_wallet_cash() or 0)
    except Exception:
        wallet = 0.0
    if wallet <= 0:
        return 1.0
    return max(1.0, round(wallet / base, 4))


def v1_stake_multiplier_for_wallet(wallet: float) -> float:
    """Wallet-tier stake multiplier for a given USDC balance."""
    cfg = load_config()
    scale = cfg.get("v1_stake_scaling") or {}
    if not scale.get("enabled", False) or wallet <= 0:
        return 1.0
    thresholds = scale.get("thresholds") or [
        {"wallet_usd": 1500, "multiplier": 1.5},
        {"wallet_usd": 2000, "multiplier": 2.0},
    ]
    mult = 1.0
    for row in sorted(thresholds, key=lambda r: float(r.get("wallet_usd") or 0)):
        if wallet >= float(row.get("wallet_usd") or 0):
            mult = float(row.get("multiplier") or mult)
    return round(mult, 4)


def _stakes_at_multiplier(mult: float) -> dict[str, Any]:
    """Fixed Package2 stakes × wallet tier (core window applies ×1.5 on top)."""
    btc = package2_config("btc")
    eth = package2_config("eth")
    bc = float(btc.get("core_stake_multiplier") or 1.5)
    ec = float(eth.get("core_stake_multiplier") or 1.5)
    m = float(mult)
    return {
        "multiplier": round(m, 2),
        "btc_fav": round(float(btc["stake_favorite"]) * m, 2),
        "btc_fav_core": round(float(btc["stake_favorite"]) * bc * m, 2),
        "btc_dog": None,
        "btc_dog_note": "disabled (BTC fav-only)",
        "eth_fav": round(float(eth["stake_favorite"]) * m, 2),
        "eth_fav_core": round(float(eth["stake_favorite"]) * ec * m, 2),
        "eth_dog": round(float(eth["stake_underdog"]) * m, 2),
        "eth_dog_core": round(float(eth["stake_underdog"]) * ec * m, 2),
    }


def v1_stake_plan_snapshot(*, wallet: float | None = None) -> dict[str, Any]:
    """Wallet tier ladder + current stakes for dashboard."""
    cfg = load_config()
    scale = cfg.get("v1_stake_scaling") or {}
    thresholds = sorted(
        scale.get("thresholds") or [],
        key=lambda r: float(r.get("wallet_usd") or 0),
    )
    if wallet is None:
        try:
            from polymarket_wallet import cached_wallet_cash

            wallet = float(cached_wallet_cash() or 0)
        except Exception:
            wallet = 0.0
    wallet = round(float(wallet or 0), 2)
    enabled = bool(scale.get("enabled", False))
    mult = v1_stake_multiplier_for_wallet(wallet) if enabled else 1.0

    active_threshold = None
    next_threshold = None
    current_idx = -1
    tiers: list[dict[str, Any]] = []
    for i, row in enumerate(thresholds):
        w = float(row.get("wallet_usd") or 0)
        m = float(row.get("multiplier") or 1)
        if enabled and wallet >= w:
            active_threshold = w
            current_idx = i
        if enabled and wallet < w and next_threshold is None:
            next_threshold = {"wallet_usd": w, "multiplier": m, "need_usd": round(w - wallet, 2)}
        stakes = _stakes_at_multiplier(m)
        tiers.append(
            {
                "wallet_usd": w,
                "multiplier": m,
                "active": enabled and wallet >= w,
                "current": False,
                **stakes,
            }
        )
    if current_idx >= 0:
        tiers[current_idx]["current"] = True

    vr = v1_risk_config()
    p2 = cfg.get("package2") or {}
    return {
        "wallet_usd": wallet,
        "scaling_enabled": enabled,
        "current_multiplier": mult,
        "current_stakes": _stakes_at_multiplier(mult),
        "active_threshold_usd": active_threshold,
        "next_threshold": next_threshold,
        "daily_stop_usd": v1_combined_daily_loss_limit_for_wallet(wallet),
        "daily_stop": v1_daily_stop_plan_snapshot(wallet=wallet),
        "btc_favorite_only": bool(p2.get("favorite_only")),
        "schedule": {
            "us_hours_et": f"Mon–Fri {p2.get('us_regular_start_et', '09:30')}–{p2.get('us_regular_end_et', '16:00')} ET",
            "core_et": f"Mon–Fri {p2.get('core_window_start_et', '11:00')}–{p2.get('core_window_end_et', '15:00')} ET",
            "core_stake_mult": float(p2.get("core_stake_multiplier") or 1.5),
            "us_weekdays_only": bool(p2.get("us_weekdays_only", True)),
            "extended_hours": False,
            "btc": "fav only · fav core-only",
            "eth": "fav core-only · dog US hours (extended off)",
            "pnl_reset": v1_trading_session_info(),
        },
        "tiers": tiers,
        "formula": "stake = base × wallet_tier × (core ×1.5 if core window); US = wallet tier only",
    }


def v1_stake_multiplier() -> float:
    """V1 wallet-threshold stake scaling: fixed USD stakes, stepped up by wallet."""
    cfg = load_config()
    scale = cfg.get("v1_stake_scaling") or {}
    if not scale.get("enabled", False):
        return 1.0
    try:
        from polymarket_wallet import cached_wallet_cash

        wallet = float(cached_wallet_cash() or 0)
    except Exception:
        wallet = 0.0
    return v1_stake_multiplier_for_wallet(wallet)


def v1_stake_startup_banner() -> list[str]:
    """Log lines for observer startup: wallet tier + expected core stakes."""
    try:
        from polymarket_wallet import cached_wallet_cash

        wallet = float(cached_wallet_cash() or 0)
    except Exception:
        wallet = 0.0
    vm = v1_stake_multiplier_for_wallet(wallet)
    plan = v1_stake_plan_snapshot(wallet=wallet)
    s = plan.get("current_stakes") or {}
    next_t = plan.get("next_threshold")
    next_s = f" · next tier {next_t['multiplier']}× at ${next_t['wallet_usd']:,.0f} (need +${next_t['need_usd']:,.2f})" if next_t else ""
    lines = [
        f"V1 wallet ${wallet:,.2f} · stake tier ×{vm:g}{next_s}",
        f"Core stakes tonight: BTC fav ${s.get('btc_fav_core', 0):,.2f} · ETH fav ${s.get('eth_fav_core', 0):,.2f} · ETH dog ${s.get('eth_dog_core', 0):,.2f}",
    ]
    if vm <= 1.0 and wallet > 0:
        lines.append("WARNING: wallet tier ×1.0 — check v1_stake_scaling.enabled and observer code reload")
    return lines


def _parse_row_timestamp_hkt(ts: str) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).replace(" HKT", "").strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=HKT)
        except ValueError:
            continue
    if len(raw) >= 10:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=HKT)
        except ValueError:
            pass
    return None


def v1_trading_session_key(dt: datetime | None = None) -> str:
    """US session bucket id (ET open date). Daily PnL / stop reset at US open (~21:30 HKT)."""
    cfg = package2_config()
    now_et = (dt or datetime.now(timezone.utc)).astimezone(ET)
    start_h, start_m = _parse_hm(cfg.get("us_regular_start_et", "09:30"))
    weekdays_only = bool(cfg.get("us_weekdays_only", True))

    d = now_et.date()
    open_today = datetime.combine(d, time(start_h, start_m), tzinfo=ET)

    if now_et < open_today:
        session_date = d - timedelta(days=1)
        if weekdays_only:
            while session_date.weekday() >= 5:
                session_date -= timedelta(days=1)
    else:
        session_date = d
        if weekdays_only and session_date.weekday() >= 5:
            while session_date.weekday() >= 5:
                session_date -= timedelta(days=1)

    return session_date.isoformat()


def next_v1_trading_session_reset_utc(now: datetime | None = None) -> datetime:
    """UTC instant of next US session open (when daily PnL / stop counter resets)."""
    now_et = (now or datetime.now(timezone.utc)).astimezone(ET)
    cfg = package2_config()
    start_h, start_m = _parse_hm(cfg.get("us_regular_start_et", "09:30"))
    weekdays_only = bool(cfg.get("us_weekdays_only", True))
    next_open = _next_us_session_open(now_et, start_h, start_m, weekdays_only=weekdays_only)
    return next_open.astimezone(timezone.utc)


def v1_trading_session_info(now: datetime | None = None) -> dict[str, Any]:
    """Session bucket + reset timing for dashboard / alerts."""
    cfg = package2_config()
    now_utc = now or datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET)
    start_h, start_m = _parse_hm(cfg.get("us_regular_start_et", "09:30"))
    end_h, end_m = _parse_hm(cfg.get("us_regular_end_et", "16:00"))
    key = v1_trading_session_key(now_utc)
    from datetime import date as date_cls

    open_et = datetime.combine(date_cls.fromisoformat(key), time(start_h, start_m), tzinfo=ET)
    close_et = datetime.combine(date_cls.fromisoformat(key), time(end_h, end_m), tzinfo=ET)
    next_reset = next_v1_trading_session_reset_utc(now_utc).astimezone(HKT)
    return {
        "session_key": key,
        "in_us_session": in_us_regular_session(now_et),
        "pnl_reset": "us_session_open",
        "pnl_resets_at_hkt": f"Mon–Fri {open_et.astimezone(HKT).strftime('%H:%M')} HKT (US open)",
        "session_open_hkt": open_et.astimezone(HKT).strftime("%a %H:%M HKT"),
        "session_close_hkt": close_et.astimezone(HKT).strftime("%a %H:%M HKT"),
        "next_reset_hkt": next_reset.strftime("%a %H:%M HKT"),
    }


def daily_realized_pnl_hkt(rows: list[dict]) -> float:
    session = v1_trading_session_key()
    total = 0.0
    for r in rows:
        if not r.get("entered"):
            continue
        ts = r.get("timestamp_hkt") or r.get("logged_at") or ""
        dt = _parse_row_timestamp_hkt(ts)
        if dt is None:
            continue
        if v1_trading_session_key(dt) != session:
            continue
        if r.get("trade_result") in ("WIN", "LOSS"):
            total += float(r.get("pnl") or 0)
    return round(total, 2)


def _default_v1_global() -> dict[str, Any]:
    return {
        "favorite_loss_streak": 0,
        "underdog_loss_streak": 0,
        "favorite_stopped": False,
        "underdog_stopped": False,
    }


def v1_risk_config() -> dict[str, Any]:
    cfg = load_config()
    vr = dict(cfg.get("v1_risk") or {})
    p2 = cfg.get("package2") or {}
    scaling = dict(vr.get("combined_daily_loss_scaling") or {})
    return {
        "combined_daily_loss_limit_usd": float(vr.get("combined_daily_loss_limit_usd", 250)),
        "combined_only_daily_stop": bool(vr.get("combined_only_daily_stop", True)),
        "combined_daily_loss_scaling": scaling,
        "cross_asset_tier_streak": bool(vr.get("cross_asset_tier_streak", True)),
        "favorite_loss_streak_pause": int(
            vr.get("favorite_loss_streak_pause") or p2.get("favorite_loss_streak_pause", 2)
        ),
        "underdog_loss_streak_pause": int(
            vr.get("underdog_loss_streak_pause") or p2.get("underdog_loss_streak_pause", 2)
        ),
    }


def v1_combined_only_daily_stop() -> bool:
    """When true, only BTC+ETH combined daily stop applies (no per-asset caps)."""
    vr = v1_risk_config()
    return bool(vr.get("combined_only_daily_stop")) and v1_combined_daily_loss_limit_for_wallet() > 0


def v1_combined_daily_loss_limit_for_wallet(wallet: float | None = None) -> float:
    """Progressive combined daily stop by wallet tier; falls back to fixed config."""
    vr = v1_risk_config()
    fallback = float(vr.get("combined_daily_loss_limit_usd") or 500)
    scaling = vr.get("combined_daily_loss_scaling") or {}
    if not scaling.get("enabled"):
        return fallback
    if wallet is None:
        try:
            from polymarket_wallet import cached_wallet_cash

            wallet = float(cached_wallet_cash() or 0)
        except Exception:
            wallet = 0.0
    wallet = float(wallet or 0)
    thresholds = sorted(
        scaling.get("thresholds") or [],
        key=lambda r: float(r.get("wallet_usd") or 0),
    )
    active = fallback
    for row in thresholds:
        w = float(row.get("wallet_usd") or 0)
        if wallet >= w:
            active = float(row.get("limit_usd") or fallback)
    return active


def v1_daily_stop_plan_snapshot(*, wallet: float | None = None) -> dict[str, Any]:
    """Wallet-tier daily stop ladder for dashboard."""
    vr = v1_risk_config()
    scaling = vr.get("combined_daily_loss_scaling") or {}
    enabled = bool(scaling.get("enabled"))
    if wallet is None:
        try:
            from polymarket_wallet import cached_wallet_cash

            wallet = float(cached_wallet_cash() or 0)
        except Exception:
            wallet = 0.0
    wallet = round(float(wallet or 0), 2)
    current = v1_combined_daily_loss_limit_for_wallet(wallet)
    thresholds = sorted(
        scaling.get("thresholds") or [],
        key=lambda r: float(r.get("wallet_usd") or 0),
    )
    current_idx = -1
    next_threshold = None
    tiers: list[dict[str, Any]] = []
    for i, row in enumerate(thresholds):
        w = float(row.get("wallet_usd") or 0)
        lim = float(row.get("limit_usd") or current)
        is_active = enabled and wallet >= w
        if is_active:
            current_idx = i
        if enabled and wallet < w and next_threshold is None:
            next_threshold = {
                "wallet_usd": w,
                "limit_usd": lim,
                "need_usd": round(w - wallet, 2),
            }
        tiers.append(
            {
                "wallet_usd": w,
                "limit_usd": lim,
                "active": is_active,
                "current": False,
            }
        )
    if current_idx >= 0:
        tiers[current_idx]["current"] = True
    return {
        "wallet_usd": wallet,
        "scaling_enabled": enabled,
        "combined_only": v1_combined_only_daily_stop(),
        "current_limit_usd": current,
        "fixed_fallback_usd": float(vr.get("combined_daily_loss_limit_usd") or 500),
        "next_threshold": next_threshold,
        "tiers": tiers,
        "formula": "combined BTC+ETH session PnL ≤ −limit → pause both · per-asset stop off · resets US open",
        "session": v1_trading_session_info(),
    }


def _read_trade_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    return rows if isinstance(rows, list) else []


def v1_combined_daily_pnl() -> float:
    btc = _read_trade_log(LIVE_TRADE_LOG_FILE)
    eth = _read_trade_log(ETH_LIVE_TRADE_LOG_FILE)
    return round(daily_realized_pnl_hkt(btc) + daily_realized_pnl_hkt(eth), 2)


def v1_combined_daily_loss_limit_usd() -> float:
    return v1_combined_daily_loss_limit_for_wallet()


def v1_combined_daily_loss_limit_hit() -> bool:
    limit = v1_combined_daily_loss_limit_usd()
    if limit <= 0:
        return False
    try:
        from bot_control import daily_stop_released

        if daily_stop_released("v1"):
            return False
    except Exception:
        pass
    return v1_combined_daily_pnl() <= -limit


def enforce_v1_combined_loss_stop() -> bool:
    """Pause V1 BTC+ETH when combined daily loss cap is hit."""
    if not v1_combined_daily_loss_limit_hit():
        return False
    try:
        from bot_control import is_bot_paused, load_bot_control, save_bot_control, set_bot_paused

        if is_bot_paused("btc") and is_bot_paused("eth"):
            return True
        limit = v1_combined_daily_loss_limit_usd()
        pnl = v1_combined_daily_pnl()
        set_bot_paused("btc", True)
        set_bot_paused("eth", True)
        data = load_bot_control()
        data["note"] = f"V1 auto-stopped: combined daily loss {pnl:+.2f} ≤ −${limit:.0f}"
        save_bot_control(data)
        from bot_common import setup_logger

        setup_logger().warning(
            "V1 combined daily loss stop: pnl=%+.2f limit=-%.0f — BTC+ETH paused",
            pnl,
            limit,
        )
        try:
            from telegram_alert import notify_v1_daily_stop

            notify_v1_daily_stop(pnl=pnl, limit=limit)
        except Exception:
            pass
        return True
    except Exception:
        return False


def session_pnl_snapshot(rows: list[dict]) -> dict[str, float]:
    """Closed-trade PnL totals for bot logging and dashboards."""
    closed = [
        r for r in rows
        if r.get("entered") and r.get("trade_result") in ("WIN", "LOSS")
    ]
    overall = round(sum(float(r.get("pnl") or 0) for r in closed), 2)
    return {
        "daily_pnl_hkt": daily_realized_pnl_hkt(rows),
        "overall_pnl": overall,
    }


def daily_loss_limit_hit(rows: list[dict], *, asset: str = "btc") -> bool:
    if v1_combined_daily_loss_limit_hit():
        return True
    if v1_combined_only_daily_stop():
        return False
    try:
        from bot_control import daily_stop_released

        if daily_stop_released(asset):
            return False
    except Exception:
        pass
    cfg = package2_config(asset)
    limit = float(cfg["daily_loss_limit_usd"])
    pnl = daily_realized_pnl_hkt(rows)
    return pnl <= -limit


def _parse_hm(raw: str) -> tuple[int, int]:
    parts = str(raw).strip().split(":")
    return int(parts[0]), int(parts[1] if len(parts) > 1 else 0)


def in_us_regular_session(now: datetime | None = None) -> bool:
    """True during US regular session (default Mon–Fri 9:30–16:00 ET)."""
    cfg = package2_config()
    if not cfg.get("us_regular_only"):
        return True
    start_h, start_m = _parse_hm(cfg.get("us_regular_start_et", "09:30"))
    end_h, end_m = _parse_hm(cfg.get("us_regular_end_et", "16:00"))
    now = now or datetime.now(ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)
    if cfg.get("us_weekdays_only", True) and now.weekday() >= 5:
        return False
    start_mins = start_h * 60 + start_m
    end_mins = end_h * 60 + end_m
    cur = now.hour * 60 + now.minute
    return start_mins <= cur < end_mins


def _format_duration(seconds: int) -> str:
    """Human-readable duration, e.g. '1h 23m' or '45m'."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return "<1m"


def _next_us_session_open(
    now_et: datetime,
    start_h: int,
    start_m: int,
    *,
    weekdays_only: bool,
) -> datetime:
    """Next Mon–Fri session open at start_h:start_m ET after now_et."""
    d = now_et.date()
    for offset in range(0, 8):
        day = d + timedelta(days=offset)
        open_dt = datetime.combine(day, time(start_h, start_m), tzinfo=ET)
        if weekdays_only and open_dt.weekday() >= 5:
            continue
        if open_dt > now_et:
            return open_dt
    return datetime.combine(d + timedelta(days=1), time(start_h, start_m), tzinfo=ET)


def us_session_timing(now: datetime | None = None) -> dict[str, Any]:
    """US trading window: time left in session or until next open."""
    cfg = package2_config()
    now_et = (now or datetime.now(ET)).astimezone(ET)
    start_h, start_m = _parse_hm(cfg.get("us_regular_start_et", "09:30"))
    end_h, end_m = _parse_hm(cfg.get("us_regular_end_et", "16:00"))
    weekdays_only = bool(cfg.get("us_weekdays_only", True))
    in_session = in_us_regular_session(now_et)

    if in_session:
        end_today = now_et.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        secs = max(0, int((end_today - now_et).total_seconds()))
        return {
            "in_session": True,
            "remaining_seconds": secs,
            "remaining_human": _format_duration(secs),
            "session_end_et": end_today.strftime("%H:%M ET"),
            "session_end_hkt": end_today.astimezone(HKT).strftime("%H:%M HKT"),
            "trading_hours_et": f"Mon–Fri {cfg.get('us_regular_start_et', '09:30')}–{cfg.get('us_regular_end_et', '16:00')} ET",
        }

    next_open = _next_us_session_open(now_et, start_h, start_m, weekdays_only=weekdays_only)
    secs = max(0, int((next_open - now_et).total_seconds()))
    return {
        "in_session": False,
        "until_next_open_seconds": secs,
        "until_next_open_human": _format_duration(secs),
        "next_open_et": next_open.strftime("%a %H:%M ET"),
        "next_open_hkt": next_open.astimezone(HKT).strftime("%a %H:%M HKT"),
        "trading_hours_et": f"Mon–Fri {cfg.get('us_regular_start_et', '09:30')}–{cfg.get('us_regular_end_et', '16:00')} ET",
    }


def load_extended_hours_live() -> dict[str, Any]:
    if not EXTENDED_HOURS_LIVE_FILE.exists():
        return {"btc_enabled": False, "eth_enabled": False, "btc_extended_dog_only": False}
    try:
        data = json.loads(EXTENDED_HOURS_LIVE_FILE.read_text())
        if "btc_enabled" not in data:
            legacy = bool(data.get("enabled", False))
            data["btc_enabled"] = legacy
            data["eth_enabled"] = legacy
        out = {
            "btc_enabled": bool(data.get("btc_enabled")),
            "eth_enabled": bool(data.get("eth_enabled")),
            "btc_extended_dog_only": bool(data.get("btc_extended_dog_only")),
            **data,
        }
        if EXTENDED_HOURS_DISABLED:
            out["btc_enabled"] = False
            out["eth_enabled"] = False
            out["btc_extended_dog_only"] = False
            out["note"] = "Extended hours disabled — BTC/ETH trade US regular hours only"
        return out
    except (json.JSONDecodeError, OSError):
        return {"btc_enabled": False, "eth_enabled": False, "btc_extended_dog_only": False}


def extended_hours_live_enabled(asset: str = "btc") -> bool:
    data = load_extended_hours_live()
    key = "btc_enabled" if asset.lower() == "btc" else "eth_enabled"
    return bool(data.get(key))


def extended_hours_dog_only(asset: str = "btc") -> bool:
    if asset.lower() != "btc":
        return False
    data = load_extended_hours_live()
    return bool(data.get("btc_enabled")) and bool(data.get("btc_extended_dog_only"))


def btc_extended_mode() -> str:
    data = load_extended_hours_live()
    if not data.get("btc_enabled"):
        return "us_hours"
    if data.get("btc_extended_dog_only"):
        return "extended_dog_only"
    return "extended"


def set_btc_extended_mode(mode: str) -> dict[str, Any]:
    if mode not in ("us_hours", "extended", "extended_dog_only"):
        raise ValueError(f"unknown btc mode: {mode}")
    if EXTENDED_HOURS_DISABLED:
        mode = "us_hours"
    now_utc = datetime.now(timezone.utc)
    now_hkt = now_utc.astimezone(HKT)
    data = load_extended_hours_live()
    data["btc_enabled"] = mode != "us_hours"
    data["btc_extended_dog_only"] = mode == "extended_dog_only"
    data["updated_at_hkt"] = now_hkt.strftime("%Y-%m-%d %H:%M:%S HKT")
    data["updated_at_utc"] = now_utc.isoformat()
    data["note"] = (
        "Extended hours disabled — BTC/ETH trade US regular hours only"
        if EXTENDED_HOURS_DISABLED
        else "Dashboard toggle — per-bot live outside US regular session (Weather/Gabagool stay paper)"
    )
    EXTENDED_HOURS_LIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXTENDED_HOURS_LIVE_FILE.write_text(json.dumps(data, indent=2))
    return get_extended_hours_live_status()


def set_extended_hours_live(*, asset: str, enabled: bool) -> dict[str, Any]:
    asset = asset.lower()
    if asset not in ("btc", "eth"):
        raise ValueError(f"unknown asset: {asset}")
    if asset == "btc":
        return set_btc_extended_mode("extended" if enabled else "us_hours")
    now_utc = datetime.now(timezone.utc)
    now_hkt = now_utc.astimezone(HKT)
    data = load_extended_hours_live()
    data[f"{asset}_enabled"] = False if EXTENDED_HOURS_DISABLED else bool(enabled)
    data["updated_at_hkt"] = now_hkt.strftime("%Y-%m-%d %H:%M:%S HKT")
    data["updated_at_utc"] = now_utc.isoformat()
    data["note"] = (
        "Extended hours disabled — BTC/ETH trade US regular hours only"
        if EXTENDED_HOURS_DISABLED
        else "Dashboard toggle — per-bot live outside US regular session (Weather/Gabagool stay paper)"
    )
    EXTENDED_HOURS_LIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXTENDED_HOURS_LIVE_FILE.write_text(json.dumps(data, indent=2))
    return get_extended_hours_live_status()


def _asset_session_status(asset: str, now: datetime | None, meta: dict[str, Any]) -> dict[str, Any]:
    cfg = package2_config()
    now_et = (now or datetime.now(ET)).astimezone(ET)
    now_hkt = now_et.astimezone(HKT)
    start_et = str(cfg.get("us_regular_start_et", "09:30"))
    end_et = str(cfg.get("us_regular_end_et", "16:00"))
    us_only = bool(cfg.get("us_regular_only"))
    in_us = in_us_regular_session(now_et) if us_only else True
    ext = bool(meta.get(f"{asset}_enabled"))
    dog_only = bool(meta.get("btc_extended_dog_only")) if asset == "btc" else False
    if asset == "btc":
        mode = "extended_dog_only" if ext and dog_only else ("extended" if ext else "us_hours")
    else:
        mode = "extended" if ext else "us_hours"
    trading_allowed = in_us or (us_only and ext)
    weekdays_only = bool(cfg.get("us_weekdays_only", True))
    hours_hkt = _et_hm_to_hkt_range(start_et, end_et, now_et)
    if weekdays_only:
        hours_et = f"Mon–Fri {start_et}–{end_et} ET"
        hours_note = f"Trade Mon–Fri {start_et}–{end_et} ET · {hours_hkt} HKT"
    else:
        hours_et = f"{start_et}–{end_et} ET"
        hours_note = f"Trade {hours_et} · {hours_hkt} HKT"
    return {
        "asset": asset,
        "enabled": ext,
        "mode": mode,
        "extended_dog_only": dog_only,
        "extended_hours_live": ext,
        "us_regular_only": us_only,
        "us_weekdays_only": weekdays_only,
        "in_us_regular_session": in_us,
        "trading_allowed": trading_allowed,
        "in_trading_session": in_us,
        "trading_hours_et": hours_et,
        "trading_hours_hkt": f"{hours_hkt} HKT",
        "trading_hours_note": hours_note,
        "now_et": now_et.strftime("%Y-%m-%d %H:%M %Z"),
        "now_hkt": now_hkt.strftime("%Y-%m-%d %H:%M %Z"),
    }


def get_extended_hours_live_status(now: datetime | None = None) -> dict[str, Any]:
    meta = load_extended_hours_live()
    btc = _asset_session_status("btc", now, meta)
    eth = _asset_session_status("eth", now, meta)
    return {
        "btc": btc,
        "eth": eth,
        "btc_enabled": btc["enabled"],
        "eth_enabled": eth["enabled"],
        "btc_extended_dog_only": bool(meta.get("btc_extended_dog_only")),
        "btc_mode": btc.get("mode", "us_hours"),
        "in_us_regular_session": btc["in_us_regular_session"],
        "trading_hours_et": btc["trading_hours_et"],
        "trading_hours_hkt": btc["trading_hours_hkt"],
        "trading_hours_note": btc["trading_hours_note"],
        "now_et": btc["now_et"],
        "now_hkt": btc["now_hkt"],
        "updated_at_hkt": meta.get("updated_at_hkt"),
        # legacy single-button consumers
        "enabled": btc["enabled"] and eth["enabled"],
        "extended_hours_live": btc["enabled"] or eth["enabled"],
        "trading_allowed": btc["trading_allowed"] or eth["trading_allowed"],
    }


def _et_hm_to_hkt_range(start_et: str, end_et: str, ref: datetime | None = None) -> str:
    sh, sm = _parse_hm(start_et)
    eh, em = _parse_hm(end_et)
    ref = (ref or datetime.now(ET)).astimezone(ET)
    start = ref.replace(hour=sh, minute=sm, second=0, microsecond=0).astimezone(HKT)
    end = ref.replace(hour=eh, minute=em, second=0, microsecond=0).astimezone(HKT)
    return f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"


def trading_session_info(now: datetime | None = None, *, asset: str = "btc") -> dict[str, Any]:
    """Trading window metadata for logs, dashboard, and backtest."""
    meta = load_extended_hours_live()
    return _asset_session_status(asset.lower(), now, meta)


def trading_session_skip_reason(now: datetime | None = None, *, asset: str = "btc") -> str:
    info = trading_session_info(now, asset=asset)
    if info["trading_allowed"]:
        return ""
    now_et = datetime.now(ET)
    if info.get("us_weekdays_only") and now_et.weekday() >= 5:
        return (
            "Skipped: weekend — strategy trades Mon–Fri only "
            f"({info['trading_hours_et']} / {info['trading_hours_hkt']})"
        )
    return (
        "Skipped: outside strategy trading period "
        f"({info['trading_hours_et']} / {info['trading_hours_hkt']}, "
        f"now {info['now_hkt'].split(' ')[1]} HKT)"
    )


def outside_trading_session(now: datetime | None = None, *, asset: str = "btc") -> bool:
    cfg = package2_config()
    if not cfg.get("us_regular_only"):
        return False
    if extended_hours_live_enabled(asset):
        return False
    return not in_us_regular_session(now)


def tier_max_ask(tier: str, ask: float) -> float:
    """Per-tier ask ceiling — favorites may run to 0.93 in strong trends."""
    cfg = package2_config()
    fav = float(cfg["favorite_ask"])
    mid_lo = float(cfg["mid_low_ask"])
    base = float(cfg["max_ask"])
    if tier == "favorite" or ask >= fav:
        return float(cfg.get("max_ask_favorite", base))
    if tier == "underdog" or ask <= mid_lo:
        return float(cfg.get("max_ask_underdog", base))
    return base


def evaluate_package2(
    ask: float,
    *,
    snapshot: Optional[dict] = None,
    ignore_session: bool = False,
    asset: str = "btc",
) -> dict[str, Any]:
    """Return enter/skip + fixed stake for Package 2+."""
    cfg = package2_config(asset)
    max_ask = float(cfg["max_ask"])
    min_ask = float(cfg["min_ask"])
    fav = float(cfg["favorite_ask"])
    mid_lo = float(cfg["mid_low_ask"])
    max_fav = float(cfg.get("max_ask_favorite", max_ask))

    if cfg.get("us_regular_only") and not ignore_session and outside_trading_session(asset=asset):
        return _skip(trading_session_skip_reason(asset=asset), category="outside_trading_period")

    if ask <= 0:
        return _skip("Skipped: no ask")
    if ask < min_ask:
        return _skip(f"Skipped: ask {ask:.2f} < min {min_ask:.2f}")

    if ask >= fav:
        tier = "favorite"
        cap = float(cfg.get("max_ask_favorite", max_ask))
    elif ask > mid_lo:
        tier = "mid"
        cap = max_ask
    else:
        tier = "underdog"
        cap = float(cfg.get("max_ask_underdog", max_ask))

    if ask > cap:
        return _skip(f"Skipped: ask {ask:.2f} > {cap:.2f} max ask ({tier})")

    if cfg.get("favorite_only") and tier != "favorite":
        return _skip(f"Skipped: favorite-only mode (ask {ask:.2f} is {tier})")

    if cfg.get("underdog_only") and tier != "underdog":
        return _skip(f"Skipped: underdog-only mode (ask {ask:.2f} is {tier})")

    if (
        asset.lower() == "btc"
        and cfg.get("us_regular_only")
        and not ignore_session
        and extended_hours_dog_only("btc")
        and not in_us_regular_session()
        and tier != "underdog"
    ):
        return _skip(
            f"Skipped: BTC extended dog-only outside US hours (ask {ask:.2f} is {tier}, fav/mid blocked)"
        )

    if (
        asset.lower() == "eth"
        and cfg.get("favorite_us_only")
        and not ignore_session
        and extended_hours_live_enabled("eth")
        and not in_us_regular_session()
        and tier == "favorite"
    ):
        return _skip(
            f"Skipped: ETH favorite US-only outside US hours (ask {ask:.2f}, extended: dog only)"
        )

    if cfg.get("skip_mid") and tier == "mid":
        return _skip(f"Skipped: mid tier disabled (ask {ask:.2f})")

    if (
        tier == "favorite"
        and cfg.get("favorite_core_only")
        and not ignore_session
        and not in_core_window(asset=asset)
    ):
        start = cfg.get("core_window_start_et", "11:00")
        end = cfg.get("core_window_end_et", "15:00")
        return _skip(
            f"Skipped: favorite core-only (ask {ask:.2f}) — fav entries Mon–Fri {start}–{end} ET only",
            category="outside_core_window",
        )

    if (
        asset.lower() == "btc"
        and tier == "favorite"
        and not ignore_session
        and datetime.now(ET).hour in set(cfg.get("btc_favorite_skip_hours_et") or [])
    ):
        hour = datetime.now(ET).hour
        return _skip(
            f"Skipped: BTC favorite disabled during {hour:02d}:00 ET hour — tracking for backtest",
            category="btc_fav_hour_filter",
        )

    if tier == "favorite":
        if favorite_paused(asset=asset):
            return _skip(streak_pause_skip_reason("favorite", asset=asset), category="loss_streak_pause")
        stake = float(cfg["stake_favorite"])
        mult = f"fav ${stake:.0f}"
        reason = f"Package 2+: favorite ask {ask:.2f} ≥ {fav:.2f} → ${stake:.0f}"
    elif tier == "mid":
        stake = float(cfg["stake_mid"])
        mult = f"mid ${stake:.0f}"
        reason = f"Package 2+: mid ask {ask:.2f} → ${stake:.0f}"
    else:
        if not ignore_session and in_weekend_dog_pause_window():
            return _skip(weekend_dog_pause_skip_reason(), category="weekend_dog_pause")
        if underdog_paused(asset=asset):
            return _skip(streak_pause_skip_reason("underdog", asset=asset), category="loss_streak_pause")
        stake = float(cfg["stake_underdog"])
        mult = f"dog ${stake:.0f}"
        reason = f"Package 2+: underdog ask {ask:.2f} → ${stake:.0f}"
        if cfg.get("underdog_extra_filters"):
            if snapshot:
                depth = float(snapshot.get("ask_depth_usd") or 0)
                dp = abs(float(snapshot.get("delta_pct") or 0))
                du = abs(float(snapshot.get("delta_usd") or 0))
                t_rem = float(snapshot.get("t_remaining") or 0)
                if depth < float(cfg["underdog_min_depth"]):
                    return _skip(f"Skipped: underdog depth ${depth:.0f} < ${cfg['underdog_min_depth']:.0f}")
                if dp < float(cfg["underdog_min_delta_pct"]) and du < float(cfg["underdog_min_move_usd"]):
                    return _skip(
                        f"Skipped: underdog weak momentum delta_{dp:.3f} move_${du:.0f}"
                    )
                if not (float(cfg["underdog_t_min"]) <= t_rem <= float(cfg["underdog_t_max"])):
                    return _skip(f"Skipped: underdog timing T-{t_rem:.0f} outside 30–60s")

    sm = core_stake_multiplier(asset)
    if sm != 1.0:
        stake = round(stake * sm, 2)
        mult = f"{tier[:3]} ${stake:.0f}" if tier == "mid" else (f"fav ${stake:.0f}" if tier == "favorite" else f"dog ${stake:.0f}")
        reason = f"{reason} · core ×{sm:g}"
        wm = core_wallet_multiplier(asset)
        if wm != 1.0:
            stake = round(stake * wm, 2)
            mult = (
                f"{tier[:3]} ${stake:.0f}"
                if tier == "mid"
                else (f"fav ${stake:.0f}" if tier == "favorite" else f"dog ${stake:.0f}")
            )
            reason = f"{reason} · wallet ×{wm:g}"

    vm = v1_stake_multiplier()
    if vm != 1.0:
        stake = round(stake * vm, 2)
        mult = (
            f"{tier[:3]} ${stake:.0f}"
            if tier == "mid"
            else (f"fav ${stake:.0f}" if tier == "favorite" else f"dog ${stake:.0f}")
        )
        reason = f"{reason} · V1 wallet tier ×{vm:g}"

    return {
        "entered": True,
        "stake": stake,
        "multiplier": mult,
        "tier": tier,
        "bankroll_used": None,
        "strategy_reason": reason,
    }


def _skip(reason: str, *, category: str = "strategy") -> dict[str, Any]:
    return {
        "entered": False,
        "stake": 0.0,
        "multiplier": "0x",
        "tier": "skip",
        "bankroll_used": None,
        "strategy_reason": reason,
        "skip_category": category,
    }


def tiers_preview(asset: str = "btc") -> dict[str, Any]:
    cfg = package2_config(asset)
    max_fav = float(cfg.get("max_ask_favorite", cfg["max_ask"]))
    fav_only = bool(cfg.get("favorite_only"))
    skip_mid = bool(cfg.get("skip_mid"))
    mode = "favorite-only" if fav_only else ("favorite+underdog" if skip_mid else "three-tier")
    examples = [
        {"ask_range": f"> {max_fav:.2f}", "multiplier": "0x", "stake": 0.0},
        {
            "ask_range": f"≥ {cfg['favorite_ask']:.2f} (≤ {max_fav:.2f})",
            "multiplier": f"fav ${cfg['stake_favorite']:.0f}",
            "stake": cfg["stake_favorite"],
        },
    ]
    if not fav_only and not skip_mid:
        examples.append({
            "ask_range": f"{cfg['mid_low_ask']:.2f} – {cfg['favorite_ask']:.2f}",
            "multiplier": f"mid ${cfg['stake_mid']:.0f}",
            "stake": cfg["stake_mid"],
        })
    elif skip_mid and not fav_only:
        examples.append({
            "ask_range": f"{cfg['mid_low_ask']:.2f} – {cfg['favorite_ask']:.2f}",
            "multiplier": f"mid ${cfg['stake_mid']:.0f}",
            "stake": cfg["stake_mid"],
            "skipped": True,
            "skip_note": "skipped due to current strategy",
        })
    if not fav_only:
        examples.append({
            "ask_range": f"{cfg['min_ask']:.2f} – {cfg['mid_low_ask']:.2f}",
            "multiplier": f"dog ${cfg['stake_underdog']:.0f}",
            "stake": cfg["stake_underdog"],
        })
    elif fav_only:
        examples.append({"ask_range": f"< {cfg['favorite_ask']:.2f}", "multiplier": "0x", "stake": 0.0})
    examples.append({"ask_range": f"< {cfg['min_ask']:.2f}", "multiplier": "0x", "stake": 0.0})
    return {
        "strategy": "PACKAGE2+",
        "mode": mode,
        "skip_mid": skip_mid,
        "trading_hours": {
            "us_regular_only": bool(cfg.get("us_regular_only")),
            "et": f"{cfg.get('us_regular_start_et', '09:30')}–{cfg.get('us_regular_end_et', '16:00')}",
            "hkt": "21:30–04:00 (approx, when US on EDT)",
        },
        "underdog_extra_filters": bool(cfg.get("underdog_extra_filters")),
        "dynamic": False,
        "bankroll": None,
        "percentages": {},
        "stakes": {
            "favorite": cfg["stake_favorite"],
            "mid": 0.0 if (fav_only or skip_mid) else cfg["stake_mid"],
            "underdog": 0.0 if fav_only else cfg["stake_underdog"],
        },
        "examples": examples,
    }
