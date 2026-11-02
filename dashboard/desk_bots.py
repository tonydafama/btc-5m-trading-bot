"""Independent Live/Paper and bet size for the two Family Desk bots.

Bet size is the number typed on that bot's own field:

- 終極5m (v2 5m btc): stake = bet size × 1.  10 → $10
- BTC 2 legs V2:       stake = bet size × 2.  10 → $20 (two legs)

Switching one bot never writes the other bot's armed flag.
Real orders stay off while the exec path is still a DRY_RUN placeholder.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HKT = ZoneInfo("Asia/Hong_Kong")

SONU_ID = "son-btc-5m-ultimate-live"
V2_ID = "dual-leg-live-v2"

MIN_BET = 0.5
MAX_BET = 500.0
MIN_LIVE_WALLET = 100.0

BOT_SPECS: dict[str, dict[str, Any]] = {
    SONU_ID: {
        "id": SONU_ID,
        "display_name": "終極5m",
        "rule_label": "v2 5m btc",
        "multiplier": 1,
        "default_bet_size": 10.0,
        "capital_usd": 100.0,
        "bet_env": "SONU_BET_SIZE",
        "stake_env": "SONU_STAKE_USD",
    },
    V2_ID: {
        "id": V2_ID,
        "display_name": "BTC 2 legs V2",
        "rule_label": "v2",
        "multiplier": 2,
        "default_bet_size": 10.0,
        "capital_usd": 100.0,
        "bet_env": "V2_BET_SIZE",
        "stake_env": "V2_STAKE_USD",
    },
}


def fmt_num(value: float) -> str:
    number = round(float(value), 2)
    if number == int(number):
        return str(int(number))
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def stake_usd_for(bot_id: str, bet_size: float) -> float:
    spec = BOT_SPECS[bot_id]
    return round(float(bet_size) * int(spec["multiplier"]), 2)


def rule_line(bot_id: str, bet_size: float) -> str:
    spec = BOT_SPECS[bot_id]
    stake = stake_usd_for(bot_id, bet_size)
    bet_txt = fmt_num(bet_size)
    stake_txt = fmt_num(stake)
    if int(spec["multiplier"]) == 2:
        return f"{spec['rule_label']}：{bet_txt} = ${stake_txt}（2 腿 × ${bet_txt}）"
    return f"{spec['rule_label']}：{bet_txt} = ${stake_txt}"


def _now_hkt() -> str:
    return datetime.now(timezone.utc).astimezone(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def _settings_path(root: Path) -> Path:
    return root / "logs" / "desk_bot_settings.json"


def _read_dotenv(root: Path) -> dict[str, str]:
    path = root / ".env"
    found: dict[str, str] = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def _write_dotenv_key(root: Path, key: str, value: str) -> None:
    path = root / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    prefix = f"{key}="
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def _load_settings(root: Path) -> dict[str, Any]:
    path = _settings_path(root)
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_settings(root: Path, data: dict[str, Any]) -> None:
    import json

    path = _settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _env_get(root: Path, env: dict[str, str] | None, key: str, default: str = "") -> str:
    if env is not None and key in env and env.get(key) is not None and str(env.get(key)).strip() != "":
        return str(env.get(key)).strip()
    return _read_dotenv(root).get(key, default)


def parse_bet_size(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError("bet size 要係數字") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("bet size 要係數字")
    value = round(value, 2)
    if value < MIN_BET or value > MAX_BET:
        raise ValueError(f"bet size 要喺 {fmt_num(MIN_BET)} 到 {fmt_num(MAX_BET)}")
    return value


def current_bet_size(root: Path, bot_id: str, env: dict[str, str] | None = None) -> float:
    spec = BOT_SPECS[bot_id]
    saved = _load_settings(root).get(bot_id) or {}
    raw = saved.get("bet_size")
    if raw is None:
        raw = _env_get(root, env, spec["bet_env"], "")
    if raw is None or str(raw).strip() == "":
        return float(spec["default_bet_size"])
    try:
        return parse_bet_size(raw)
    except ValueError:
        return float(spec["default_bet_size"])


def bet_view(root: Path, bot_id: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    spec = BOT_SPECS[bot_id]
    bet = current_bet_size(root, bot_id, env)
    stake = stake_usd_for(bot_id, bet)
    return {
        "bet_size": bet,
        "bet_multiplier": int(spec["multiplier"]),
        "stake_usd": fmt_num(stake),
        "stake_label": f"${fmt_num(stake)}/注",
        "per_leg_usd": fmt_num(bet if int(spec["multiplier"]) == 2 else stake),
        "rule_label": spec["rule_label"],
        "rule_line": rule_line(bot_id, bet),
        "capital_usd": float(spec["capital_usd"]),
    }


def decorate_bet(root: Path, row: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any]:
    bot_id = str(row.get("id") or "")
    if bot_id not in BOT_SPECS:
        return row
    row.update(bet_view(root, bot_id, env))
    return row


def v2_exec_is_real(root: Path, env: dict[str, str] | None = None) -> bool:
    dry = _env_get(root, env, "DUAL_LEG_LIVE_DRY_RUN", "1")
    real_money = _env_get(root, env, "DUAL_LEG_REAL_MONEY", "0")
    return (not _truthy(dry)) and _truthy(real_money)


def v2_mode_view(root: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    exec_real = v2_exec_is_real(root, env)
    armed = _truthy(_env_get(root, env, "V2_LIVE_ARMED_PREP", "0"))
    live_active = bool(exec_real and armed)
    if live_active:
        mode = "live"
        status_label = "跑緊 · 真倉 ON"
        change = "Live · 真倉 ON"
        label_tc = "而家模式：Live（真倉 ON）"
    elif armed:
        mode = "prep"
        status_label = "紙上 armed · 真倉 OFF"
        change = "PREP · DRY_RUN · 真倉未開"
        label_tc = "PREP · 紙上 armed · 真倉未開"
    else:
        mode = "paper"
        status_label = "曾live · 紙上 rehearsal · 真倉 OFF"
        change = "Paper · DRY_RUN · 真倉 OFF"
        label_tc = "而家模式：Paper（紙上）"
    return {
        "mode": mode,
        "live_active": live_active,
        "live_armed": armed and not live_active,
        "live_armed_prep": armed and not exec_real,
        "exec_is_real_live": exec_real,
        "exec_dry_run_placeholder": not exec_real,
        "status_label": status_label,
        "change": change,
        "label_tc": label_tc,
    }


def _persist_bet_env(root: Path, bot_id: str, bet: float) -> None:
    spec = BOT_SPECS[bot_id]
    stake = stake_usd_for(bot_id, bet)
    _write_dotenv_key(root, spec["bet_env"], fmt_num(bet))
    _write_dotenv_key(root, spec["stake_env"], fmt_num(stake))
    if bot_id == V2_ID:
        _write_dotenv_key(root, "DUAL_LEG_UNIT_USD", fmt_num(bet))
        _write_dotenv_key(root, "DUAL_LEG_TOTAL_STAKE_USD", fmt_num(stake))


def set_bet_size(root: Path, bot_id: str, raw: Any) -> dict[str, Any]:
    if bot_id not in BOT_SPECS:
        raise ValueError("未知 bot")
    bet = parse_bet_size(raw)
    settings = _load_settings(root)
    settings[bot_id] = {
        "bet_size": bet,
        "updated_at_hkt": _now_hkt(),
    }
    _save_settings(root, settings)
    _persist_bet_env(root, bot_id, bet)
    spec = BOT_SPECS[bot_id]
    view = bet_view(root, bot_id)
    view.update(
        {
            "ok": True,
            "bot_id": bot_id,
            "display_name": spec["display_name"],
            "message_tc": (
                f"{spec['display_name']} 注碼已儲存：bet size {fmt_num(bet)} → {view['rule_line']}"
            ),
        }
    )
    return view


def _apply_v2_mode(root: Path, action: str, *, wallet_cash_usdc: float | None) -> dict[str, Any]:
    current = v2_mode_view(root)
    if action == "to_paper":
        _write_dotenv_key(root, "V2_LIVE_ARMED_PREP", "0")
        _write_dotenv_key(root, "DUAL_LEG_TRADING_MODE", "paper")
        out = v2_mode_view(root)
        out.update(
            {
                "ok": True,
                "bot_id": V2_ID,
                "action": action,
                "display_name": BOT_SPECS[V2_ID]["display_name"],
                "message_tc": "BTC 2 legs V2 已切換 → Paper（真倉 OFF）",
            }
        )
        return out

    if v2_exec_is_real(root):
        if wallet_cash_usdc is not None and float(wallet_cash_usdc) < MIN_LIVE_WALLET:
            return {
                **current,
                "ok": False,
                "bot_id": V2_ID,
                "action": action,
                "error": "wallet_gate",
                "error_tc": (
                    f"銀包 ${float(wallet_cash_usdc):.2f} 未達 ${MIN_LIVE_WALLET:.0f}，V2 唔會開真倉"
                ),
            }
        _write_dotenv_key(root, "V2_LIVE_ARMED_PREP", "1")
        _write_dotenv_key(root, "DUAL_LEG_TRADING_MODE", "live")
        out = v2_mode_view(root)
        out.update(
            {
                "ok": True,
                "bot_id": V2_ID,
                "action": action,
                "display_name": BOT_SPECS[V2_ID]["display_name"],
                "message_tc": "BTC 2 legs V2 已請求 Live 真倉",
            }
        )
        return out

    _write_dotenv_key(root, "V2_LIVE_ARMED_PREP", "1")
    _write_dotenv_key(root, "DUAL_LEG_TRADING_MODE", "paper")
    out = v2_mode_view(root)
    out.update(
        {
            "ok": True,
            "bot_id": V2_ID,
            "action": action,
            "display_name": BOT_SPECS[V2_ID]["display_name"],
            "message_tc": "BTC 2 legs V2 已 armed prep（紙上）· 真倉未開，唔會動真錢",
        }
    )
    return out


def _apply_sonu_mode(root: Path, action: str, *, wallet_cash_usdc: float | None) -> dict[str, Any]:
    from sonu_live_truth import apply_live_control_action

    mapped = "to_live" if action == "to_live" else "to_paper"
    out = apply_live_control_action(root, mapped, wallet_cash_usdc=wallet_cash_usdc)
    if out.get("live_active"):
        desk_mode = "live"
    elif out.get("live_armed_prep") or out.get("live_armed"):
        desk_mode = "prep"
    else:
        desk_mode = "paper"
    out["bot_id"] = SONU_ID
    out["desk_mode"] = desk_mode
    out["display_name"] = BOT_SPECS[SONU_ID]["display_name"]
    if out.get("ok") and action == "to_paper":
        out["message_tc"] = "終極5m 已切換 → Paper（真倉 OFF）"
    elif out.get("ok") and action == "to_live" and desk_mode == "prep":
        out["message_tc"] = "終極5m 已 armed prep（紙上）· 真倉未開，唔會動真錢"
    elif out.get("ok") and action == "to_live":
        out["message_tc"] = "終極5m 已請求 Live 真倉"
    return out


def apply_mode(root: Path, bot_id: str, action: str, *, wallet_cash_usdc: float | None = None) -> dict[str, Any]:
    action = (action or "").strip().lower()
    if bot_id not in BOT_SPECS:
        return {"ok": False, "error": "unknown bot", "error_tc": "未知 bot", "bot_id": bot_id}
    if action not in ("to_live", "to_paper"):
        return {
            "ok": False,
            "error": "bad_action",
            "error_tc": "action 要係 to_live、to_paper 或 set_bet_size",
            "bot_id": bot_id,
        }
    if bot_id == V2_ID:
        return _apply_v2_mode(root, action, wallet_cash_usdc=wallet_cash_usdc)
    return _apply_sonu_mode(root, action, wallet_cash_usdc=wallet_cash_usdc)


def handle_desk_action(
    root: Path,
    bot_id: str,
    action: str,
    *,
    bet_size: Any = None,
    wallet_cash_usdc: float | None = None,
) -> dict[str, Any]:
    action = (action or "").strip().lower()
    if bot_id not in BOT_SPECS:
        return {"ok": False, "error": "unknown bot", "error_tc": "未知 bot", "bot_id": bot_id}
    if action == "set_bet_size":
        try:
            return set_bet_size(root, bot_id, bet_size)
        except ValueError as exc:
            return {"ok": False, "error": "bad_bet_size", "error_tc": str(exc), "bot_id": bot_id}
    return apply_mode(root, bot_id, action, wallet_cash_usdc=wallet_cash_usdc)


def desk_status(root: Path, *, wallet_cash_usdc: float | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    from sonu_live_truth import live_control_payload

    sonu = live_control_payload(root, wallet_cash_usdc=wallet_cash_usdc)
    if sonu.get("live_active"):
        sonu_mode = "live"
    elif sonu.get("live_armed_prep") or sonu.get("live_armed"):
        sonu_mode = "prep"
    else:
        sonu_mode = "paper"
    v2 = v2_mode_view(root, env)
    bots = []
    for bot_id, mode, extra in (
        (SONU_ID, sonu_mode, sonu),
        (V2_ID, v2["mode"], v2),
    ):
        spec = BOT_SPECS[bot_id]
        bots.append(
            {
                "id": bot_id,
                "display_name": spec["display_name"],
                "mode": mode,
                "label_tc": extra.get("label_tc"),
                "live_active": bool(extra.get("live_active")),
                "exec_dry_run_placeholder": bool(extra.get("exec_dry_run_placeholder", not extra.get("exec_is_real_live", False))),
                **bet_view(root, bot_id, env),
            }
        )
    return {"ok": True, "bots": bots, "wallet_cash_usdc": wallet_cash_usdc}
