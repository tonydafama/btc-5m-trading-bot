"""Unified strategy copy for dashboard bot pages — sourced from strategy_config + extended hours."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from package2_strategy import WEEKEND_DOG_PAUSE, in_weekend_dog_pause_window

ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")

_in_weekend_dog_pause_window = in_weekend_dog_pause_window

def _in_core_window_et(now_et: datetime, p2: dict) -> bool:
    if now_et.weekday() >= 5:
        return False
    sh, sm = (int(x) for x in str(p2.get("core_window_start_et") or "11:00").split(":")[:2])
    eh, em = (int(x) for x in str(p2.get("core_window_end_et") or "15:00").split(":")[:2])
    cur = now_et.hour * 60 + now_et.minute
    return sh * 60 + sm <= cur < eh * 60 + em


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _core(base: Any, mult: Any) -> int:
    return _i(_f(base) * _f(mult, 1.5))


def _wallet_mult(p2: dict, config: dict | None = None) -> float:
    if (config or {}).get("v1_stake_scaling", {}).get("enabled"):
        try:
            from package2_strategy import v1_stake_multiplier

            return v1_stake_multiplier()
        except Exception:
            return 1.0
    if not p2.get("core_wallet_scaling"):
        return 1.0
    base = _f(p2.get("core_wallet_base_usd"), 1000.0)
    if base <= 0:
        return 1.0
    try:
        from polymarket_wallet import cached_wallet_cash

        wallet = cached_wallet_cash()
    except Exception:
        wallet = 0.0
    return max(1.0, wallet / base) if wallet > 0 else 1.0


def _btc_hours_label(ext: dict, p2: dict | None = None) -> str:
    mode = ext.get("mode") or ("extended" if ext.get("enabled") else "us_hours")
    fav_core = bool((p2 or {}).get("favorite_core_only"))
    if mode == "extended_dog_only":
        return "Extended · 非美股 Dog only"
    if fav_core and (mode == "extended" or ext.get("enabled")):
        return "US · Fav core-only 11–15 ET · Dog US+extended"
    if mode == "extended" or ext.get("enabled"):
        return "Extended full · 24h fav + dog"
    if fav_core:
        return "US Hours · Fav core-only 11–15 ET · Dog US regular"
    return "US Hours · Mon–Fri 9:30–16 ET"


def _eth_hours_label(eth_cfg: dict, ext: dict) -> str:
    fav_us = bool(eth_cfg.get("favorite_us_only"))
    fav_core = bool(eth_cfg.get("favorite_core_only"))
    ext_on = bool(ext.get("enabled"))
    if fav_us and fav_core and ext_on:
        return "Hybrid · Fav US core-only 11–15 ET · Dog Extended 24h"
    if fav_us and ext_on:
        return "Hybrid · Fav 僅 US 時段 · Dog Extended 24h"
    if fav_us:
        return "Fav US-only · Dog US Hours"
    if ext_on:
        return "Extended full · 24h"
    return "US Hours · Mon–Fri 9:30–16 ET"


def build_strategy_display(
    config: dict[str, Any],
    extended_hours: dict[str, Any],
    *,
    entry_window: str = "T-75 → T-22",
    eth_rules: dict[str, Any] | None = None,
    btc_live: bool = False,
    eth_live: bool = False,
) -> dict[str, Any]:
    p2 = config.get("package2") or {}
    eth = config.get("eth_package2") or {}
    plan = config.get("capital_plan") or {}
    eth_rules = eth_rules or {}

    core_mult = _f(p2.get("core_stake_multiplier"), 1.5)
    eth_core_mult = _f(eth.get("core_stake_multiplier"), core_mult)
    wallet_mult = _wallet_mult(p2, config)

    btc_fav_only = bool(p2.get("favorite_only"))
    btc_fav = _i(p2.get("stake_favorite"), 54)
    btc_dog = _i(p2.get("stake_underdog"), 90)
    btc_fav_scaled = _i(btc_fav * wallet_mult)
    btc_fav_core = _core(btc_fav, core_mult)
    btc_fav_core_dyn = _i(btc_fav_core * wallet_mult)
    if btc_fav_only:
        btc_dog_scaled = 0
        btc_dog_core_dyn = 0
    else:
        btc_dog_scaled = _i(btc_dog * wallet_mult)
        btc_dog_core = _core(btc_dog, core_mult)
        btc_dog_core_dyn = _i(btc_dog_core * wallet_mult)

    eth_fav = _i(eth.get("stake_favorite"), 48)
    eth_dog = _i(eth.get("stake_underdog"), 90)
    eth_fav_scaled = _i(eth_fav * wallet_mult)
    eth_dog_scaled = _i(eth_dog * wallet_mult)
    eth_fav_core = _core(eth_fav, eth_core_mult)
    eth_dog_core = _core(eth_dog, eth_core_mult)
    eth_fav_core_dyn = _i(eth_fav_core * wallet_mult)
    eth_dog_core_dyn = _i(eth_dog_core * wallet_mult)

    btc_ext = extended_hours.get("btc") or {}
    eth_ext = extended_hours.get("eth") or {}
    btc_hours = _btc_hours_label(btc_ext, p2)
    eth_hours = _eth_hours_label(eth, eth_ext)

    plan_mode = plan.get("mode") or "A_hybrid"
    capital = _i(plan.get("base_usd"), 1000)
    plan_note = plan.get("note") or config.get("note") or ""

    pause_n = _i(p2.get("favorite_loss_streak_pause"), 2)
    pause_h = _i(p2.get("tier_pause_hours"), 2)
    pause_rule = f"同 tier 連續 {pause_n} 敗 → 暫停 {pause_h}h（fav / dog 分開 · BTC / ETH 分開）"

    us_start = p2.get("us_regular_start_et") or "09:30"
    us_end = p2.get("us_regular_end_et") or "16:00"
    us_hours = f"Mon–Fri {us_start}–{us_end} ET（21:30–04:00 HKT）"
    core_window = f"Mon–Fri {p2.get('core_window_start_et') or '11:00'}–{p2.get('core_window_end_et') or '15:00'} ET"
    wallet_note = ""
    v1_scale = (config or {}).get("v1_stake_scaling") or {}
    if v1_scale.get("enabled") and wallet_mult > 1:
        wallet_note = f" · V1 wallet tier ×{wallet_mult:g}"
    elif p2.get("core_wallet_scaling") and wallet_mult > 1:
        wallet_note = f" · core wallet ×{wallet_mult:.2f}"

    fav_ask = _f(p2.get("favorite_ask"), 0.70)
    mid_lo = _f(p2.get("mid_low_ask"), 0.30)
    min_ask = _f(p2.get("min_ask"), 0.15)
    max_fav = _f(p2.get("max_ask_favorite"), 0.85)
    max_dog = _f(p2.get("max_ask_underdog") or p2.get("max_ask"), 0.88)
    skip_mid = bool(p2.get("skip_mid"))

    btc_daily = _i(p2.get("daily_loss_limit_usd"), 225)
    eth_daily = _i(eth.get("daily_loss_limit_usd"), 450)
    try:
        from package2_strategy import v1_combined_daily_loss_limit_usd, v1_combined_only_daily_stop

        if v1_combined_only_daily_stop():
            combined_daily = _i(v1_combined_daily_loss_limit_usd())
            btc_daily = combined_daily
            eth_daily = combined_daily
    except Exception:
        pass

    eth_entry = eth_rules.get("entry_window") or entry_window
    eth_move = eth_rules.get("min_eth_move_usd", 1.0)
    eth_delta = eth_rules.get("min_delta_pct", 0.025)
    eth_depth = eth_rules.get("min_depth_usd", 30)

    btc_mode_tag = "LIVE" if btc_live else "PAPER"
    eth_mode_tag = "LIVE" if eth_live else "PAPER"

    mid_note = " <span style=\"color:#8b949e\">(略過 mid)</span>" if skip_mid else ""
    if btc_fav_only:
        btc_stakes = (
            f"ask ≥ {fav_ask:.2f} → <strong>${btc_fav_scaled}</strong>（core ${btc_fav_core_dyn}）"
            f" · underdog <strong>off</strong>（BTC fav-only）"
        )
    else:
        btc_stakes = (
            f"ask ≥ {fav_ask:.2f} → <strong>${btc_fav_scaled}</strong>（core ${btc_fav_core_dyn}）"
            f" · {mid_lo:.2f}–{fav_ask:.2f} → mid ${ _i(p2.get('stake_mid'), 10)}{mid_note}"
            f" · {min_ask:.2f}–{mid_lo:.2f} → <strong>${btc_dog_scaled}</strong>（core ${btc_dog_core_dyn}）"
        )
    eth_stakes = (
        f"ask ≥ {fav_ask:.2f} → <strong>${eth_fav_scaled}</strong>（core ${eth_fav_core_dyn}）"
        f" · {mid_lo:.2f}–{fav_ask:.2f} → skip mid"
        f" · {min_ask:.2f}–{mid_lo:.2f} → <strong>${eth_dog_scaled}</strong>（core ${eth_dog_core_dyn}）"
    )

    btc_rules_extra = (
        f"ask &lt; {min_ask:.2f} → SKIP · fav ≤ {max_fav:.2f} · dog ≤ {max_dog:.2f}"
        f" · 日虧上限 ${btc_daily} · {pause_rule} · 進場 {entry_window}"
    )
    eth_rules_extra = (
        f"進場 {eth_entry} · 日虧上限 ${eth_daily} · {pause_rule}"
    )

    btc_mode = btc_ext.get("mode") or (
        "extended_dog_only" if btc_ext.get("extended_dog_only")
        else ("extended" if btc_ext.get("enabled") else "us_hours")
    )
    if p2.get("favorite_core_only"):
        btc_fav_sched = f"Core only · {core_window}"
    else:
        btc_fav_sched = f"US hours · {us_hours}"
    if btc_mode == "extended_dog_only":
        btc_dog_sched = "非美股 Dog only · 美股 fav + dog"
    elif btc_ext.get("enabled"):
        btc_dog_sched = "24h Extended（美股 + 非美股）"
    else:
        btc_dog_sched = f"US hours · {us_hours}"

    eth_fav_sched = (
        f"US Core only · {core_window}"
        if eth.get("favorite_us_only") and eth.get("favorite_core_only")
        else (f"US hours only · {us_hours}" if eth.get("favorite_us_only") else "依窗口")
    )
    eth_dog_sched = (
        "24h Extended（含非美股）"
        if eth_ext.get("enabled")
        else f"US hours · {us_hours}"
    )

    btc_filter_list = [
        f"Entry {entry_window}",
        f"Max ask · fav ≤ {max_fav:.2f} · dog ≤ {max_dog:.2f}",
    ]
    skip_hours = p2.get("btc_favorite_skip_hours_et") or []
    if skip_hours:
        btc_filter_list.append("Skip fav · ET " + ", ".join(f"{h:02d}:00" for h in skip_hours))

    eth_filter_list = [
        f"Entry {eth_entry}",
        f"Fav · eth_move ≥ ${eth_move} · delta ≥ {eth_delta}% · depth ≥ ${eth_depth}",
        f"Max ask · fav ≤ {max_fav:.2f} · dog ≤ {max_dog:.2f}",
    ]

    wallet_line = ""
    if v1_scale.get("enabled") and wallet_mult > 1:
        wallet_line = f"V1 wallet tier ×{wallet_mult:g}"
    elif wallet_mult > 1:
        wallet_line = f"Wallet scaling ×{wallet_mult:.2f}"
    core_mult_line = f"Core window {core_window} · stake ×{core_mult:g}"

    if v1_scale.get("enabled"):
        th = sorted(v1_scale.get("thresholds") or [], key=lambda r: float(r.get("wallet_usd") or 0))
        wallet_rule = "V1 wallet tier · " + " · ".join(
            f"${_i(t.get('wallet_usd'))}→×{_f(t.get('multiplier'), 1):g}" for t in th
        )
    elif p2.get("core_wallet_scaling"):
        wallet_rule = f"Wallet scaling · base $1000 · 現 ×{wallet_mult:.2f}"
    else:
        wallet_rule = "Wallet scaling · off"

    def _rules_card(
        *,
        asset: str,
        fav: int,
        dog: int,
        fav_core_dyn: int,
        dog_core_dyn: int,
        daily: int,
        hours_summary: str,
        fav_schedule: str,
        dog_schedule: str,
        entry: str,
        filters: list[str],
        core_mult_note: str,
        live: bool,
        dog_disabled: bool = False,
    ) -> dict[str, Any]:
        dog_base = "off" if dog_disabled else f"${dog}"
        dog_core = "off" if dog_disabled else f"${dog_core_dyn}"
        return {
            "asset": asset,
            "mode": "LIVE" if live else "PAPER",
            "hours_summary": hours_summary,
            "stakes": [
                {"tier": "Favorite", "ask": f"≥ {fav_ask:.2f}", "base": f"${fav}", "core": f"${fav_core_dyn}"},
                {"tier": "Mid", "ask": f"{mid_lo:.2f}–{fav_ask:.2f}", "base": "—", "core": "skip"},
                {"tier": "Underdog", "ask": f"{min_ask:.2f}–{mid_lo:.2f}", "base": dog_base, "core": dog_core},
            ],
            "schedule": [
                {"tier": "Favorite", "text": fav_schedule},
                {"tier": "Underdog", "text": dog_schedule},
            ],
            "entry_window": entry,
            "filters": filters,
            "core_mult_note": core_mult_note,
            "wallet_line": wallet_line,
            "daily_loss": daily,
            "pause_rule": pause_rule,
        }

    btc_hours_short = _btc_hours_label(btc_ext, p2)
    eth_hours_short = _eth_hours_label(eth, eth_ext)

    btc_panel_sub = (
        f"BTC 5m · {btc_mode_tag} · {btc_hours_short}"
        + (f" · ×{wallet_mult:.2f}" if wallet_mult > 1 else "")
        + f" · daily −${btc_daily}"
    )
    eth_panel_sub = (
        f"ETH 5m · {eth_mode_tag} · {eth_hours_short}"
        + (f" · ×{wallet_mult:.2f}" if wallet_mult > 1 else "")
        + f" · daily −${eth_daily}"
    )

    btc_card = _rules_card(
        asset="BTC",
        fav=btc_fav_scaled,
        dog=btc_dog_scaled,
        fav_core_dyn=btc_fav_core_dyn,
        dog_core_dyn=btc_dog_core_dyn,
        daily=btc_daily,
        hours_summary=btc_hours_short,
        fav_schedule=btc_fav_sched,
        dog_schedule=btc_dog_sched,
        entry=entry_window,
        filters=btc_filter_list,
        core_mult_note=core_mult_line,
        live=btc_live,
        dog_disabled=btc_fav_only,
    )
    eth_card = _rules_card(
        asset="ETH",
        fav=eth_fav_scaled,
        dog=eth_dog_scaled,
        fav_core_dyn=eth_fav_core_dyn,
        dog_core_dyn=eth_dog_core_dyn,
        daily=eth_daily,
        hours_summary=eth_hours_short,
        fav_schedule=eth_fav_sched,
        dog_schedule=eth_dog_sched,
        entry=eth_entry,
        filters=eth_filter_list,
        core_mult_note=f"Core window {core_window} · stake ×{eth_core_mult:g}",
        live=eth_live,
    )

    timetable = _build_timetable(
        p2=p2,
        eth=eth,
        btc_ext=btc_ext,
        eth_ext=eth_ext,
        extended_hours=extended_hours,
        pause_rule=pause_rule,
        core_window=core_window,
        us_hours=us_hours,
        entry_window=entry_window,
        eth_entry=eth_entry,
        btc_card=btc_card,
        eth_card=eth_card,
        wallet_mult=wallet_mult,
        core_mult=core_mult,
        eth_core_mult=eth_core_mult,
        max_fav=max_fav,
        max_dog=max_dog,
        min_ask=min_ask,
        fav_ask=fav_ask,
        mid_lo=mid_lo,
        btc_daily=btc_daily,
        eth_daily=eth_daily,
        wallet_rule=wallet_rule,
    )

    return {
        "plan_mode": plan_mode,
        "plan_note": plan_note,
        "capital_usd": capital,
        "pause_rule": pause_rule,
        "core_window": core_window,
        "us_hours": us_hours,
        "entry_window": entry_window,
        "timetable": timetable,
        "btc": {
            "panel_sub": btc_panel_sub,
            "stakes_html": btc_stakes,
            "rules_html": btc_rules_extra,
            "rules_card": btc_card,
            "hours_mode": btc_hours,
            "fav": btc_fav_scaled,
            "dog": btc_dog_scaled,
            "fav_core": btc_fav_core_dyn,
            "dog_core": btc_dog_core_dyn if not btc_fav_only else 0,
            "daily_loss": btc_daily,
        },
        "eth": {
            "panel_sub": eth_panel_sub,
            "stakes_html": eth_stakes,
            "rules_html": eth_rules_extra,
            "rules_card": eth_card,
            "hours_mode": eth_hours,
            "fav": eth_fav_scaled,
            "dog": eth_dog_scaled,
            "fav_core": eth_fav_core_dyn,
            "dog_core": eth_dog_core_dyn,
            "daily_loss": eth_daily,
            "favorite_us_only": bool(eth.get("favorite_us_only")),
            "favorite_core_only": bool(eth.get("favorite_core_only")),
        },
        "btc_favorite_core_only": bool(p2.get("favorite_core_only")),
    }


def _build_timetable(
    *,
    p2: dict,
    eth: dict,
    btc_ext: dict,
    eth_ext: dict,
    extended_hours: dict,
    pause_rule: str,
    core_window: str,
    us_hours: str,
    entry_window: str,
    eth_entry: str,
    btc_card: dict,
    eth_card: dict,
    wallet_mult: float,
    core_mult: float,
    eth_core_mult: float,
    max_fav: float,
    max_dog: float,
    min_ask: float,
    fav_ask: float,
    mid_lo: float,
    btc_daily: int,
    eth_daily: int,
    wallet_rule: str = "Wallet scaling · off",
) -> dict[str, Any]:
    from package2_strategy import in_us_regular_session

    now_et = datetime.now(ET)
    now_hkt = now_et.astimezone(HKT)
    in_us = in_us_regular_session(now_et)
    in_core = _in_core_window_et(now_et, p2)
    in_dog_pause_win = _in_weekend_dog_pause_window(now_et)

    def _dog_pause_note() -> str:
        if WEEKEND_DOG_PAUSE["enabled"]:
            return "週末 dog 暫停中" if in_dog_pause_win else ""
        if WEEKEND_DOG_PAUSE.get("pending") and in_dog_pause_win:
            return "待上線：此時段應停 dog（尚未套用）"
        return ""

    def _btc_fav_status() -> dict[str, str]:
        if not btc_ext.get("enabled") and not in_us:
            return {"state": "blocked", "label": "阻擋", "detail": "US hours 外且 Extended 關"}
        if p2.get("favorite_core_only") and not in_core:
            return {"state": "blocked", "label": "阻擋", "detail": f"Favorite core-only · {core_window}"}
        hour = now_et.hour
        if hour in set(p2.get("btc_favorite_skip_hours_et") or []):
            return {"state": "blocked", "label": "阻擋", "detail": f"Skip fav · ET {hour:02d}:00"}
        if in_core:
            return {"state": "active", "label": "可下", "detail": "Core 時段 · stake ×1.5 + wallet scale"}
        return {"state": "blocked", "label": "阻擋", "detail": "非 core 時段"}

    def _btc_dog_status() -> dict[str, str]:
        if not btc_ext.get("enabled") and not in_us:
            return {"state": "blocked", "label": "阻擋", "detail": "US hours 外且 Extended 關"}
        note = _dog_pause_note()
        if note:
            state = "blocked" if WEEKEND_DOG_PAUSE["enabled"] else "pending"
            label = "已停" if WEEKEND_DOG_PAUSE["enabled"] else "待停"
            return {"state": state, "label": label, "detail": note}
        if in_core:
            return {"state": "active", "label": "可下", "detail": "Core · stake ×1.5 + wallet scale"}
        if btc_ext.get("enabled"):
            return {"state": "active", "label": "可下", "detail": "Extended · base stake $90"}
        return {"state": "active", "label": "可下", "detail": "US hours · base stake"}

    def _eth_fav_status() -> dict[str, str]:
        if eth.get("favorite_us_only") and not in_us:
            return {"state": "blocked", "label": "阻擋", "detail": "Favorite US-only · 非美股時段"}
        if eth.get("favorite_core_only") and not in_core:
            return {"state": "blocked", "label": "阻擋", "detail": f"Favorite core-only · {core_window}"}
        if in_core and in_us:
            return {"state": "active", "label": "可下", "detail": "US Core · stake ×1.5 + wallet scale"}
        return {"state": "blocked", "label": "阻擋", "detail": "非 US core 時段"}

    def _eth_dog_status() -> dict[str, str]:
        if not eth_ext.get("enabled") and not in_us:
            return {"state": "blocked", "label": "阻擋", "detail": "US hours 外且 Extended 關"}
        note = _dog_pause_note()
        if note:
            state = "blocked" if WEEKEND_DOG_PAUSE["enabled"] else "pending"
            label = "已停" if WEEKEND_DOG_PAUSE["enabled"] else "待停"
            return {"state": state, "label": label, "detail": note}
        if in_core:
            return {"state": "active", "label": "可下", "detail": "Core · stake ×1.5 + wallet scale"}
        if eth_ext.get("enabled"):
            return {"state": "active", "label": "可下", "detail": "Extended 24h · base stake $90"}
        return {"state": "active", "label": "可下", "detail": "US hours · base stake"}

    operation_rows = [
        {
            "name": "US regular · 最佳流動性",
            "et": "Mon–Fri 09:30–16:00 ET",
            "hkt": "Mon–Fri 21:30–翌日 04:00 HKT",
            "note": "V1 正常跑 · fav + dog",
            "tone": "good",
        },
        {
            "name": "Friday after close",
            "et": "Fri 16:00–Sat 00:00 ET",
            "hkt": "Sat 04:00–12:00 HKT",
            "note": "Extended 已停用 · US hours 外不下單",
            "tone": "warn",
        },
        {
            "name": "Weekend dog pause · 已上線" if WEEKEND_DOG_PAUSE["enabled"] else "Weekend dog pause（待上線）",
            "et": WEEKEND_DOG_PAUSE["et_window"],
            "hkt": WEEKEND_DOG_PAUSE["hkt_window"],
            "note": "只停 BTC/ETH dog · fav 不變" if WEEKEND_DOG_PAUSE["enabled"] else "批准後：只停 BTC/ETH dog · fav 不變",
            "tone": "danger",
        },
        {
            "name": "Monday pre-market",
            "et": "Mon 00:00–09:30 ET",
            "hkt": "Mon 12:00–21:30 HKT",
            "note": "fav 等 21:30 HKT reopen · dog 待停至 21:30",
            "tone": "warn",
        },
        {
            "name": "Monday regular reopen",
            "et": "Mon 09:30 ET 起",
            "hkt": "Mon 21:30 HKT 起",
            "note": "正常恢復",
            "tone": "good",
        },
    ]

    return {
        "now_et": now_et.strftime("%a %Y-%m-%d %H:%M %Z"),
        "now_hkt": now_hkt.strftime("%a %Y-%m-%d %H:%M %Z"),
        "weekend_dog_pause": {
            **WEEKEND_DOG_PAUSE,
            "in_window_now": in_dog_pause_win,
            "bot_enforced": WEEKEND_DOG_PAUSE["enabled"],
        },
        "live_status": [
            {"asset": "BTC", "tier": "Favorite", **_btc_fav_status()},
            {"asset": "BTC", "tier": "Underdog", **_btc_dog_status()},
            {"asset": "ETH", "tier": "Favorite", **_eth_fav_status()},
            {"asset": "ETH", "tier": "Underdog", **_eth_dog_status()},
        ],
        "operation_rows": operation_rows,
        "stakes": {"btc": btc_card["stakes"], "eth": eth_card["stakes"]},
        "shared_rules": [
            f"Core window · {core_window} · stake ×{core_mult:g}（ETH ×{eth_core_mult:g}）",
            wallet_rule,
            f"Entry · BTC {entry_window} · ETH {eth_entry}",
            f"Ask · fav ≥ {fav_ask:.2f} cap {max_fav:.2f} · dog {min_ask:.2f}–{mid_lo:.2f} cap {max_dog:.2f} · mid skip",
            f"Daily loss · BTC+ETH combined −${btc_daily} (per-asset off)",
            pause_rule,
            "Trading hours · Extended disabled · BTC/ETH 僅 US regular hours",
        ],
        "schedule_detail": {
            "btc_fav": btc_card["schedule"][0]["text"],
            "btc_dog": btc_card["schedule"][1]["text"],
            "eth_fav": eth_card["schedule"][0]["text"],
            "eth_dog": eth_card["schedule"][1]["text"],
        },
    }
