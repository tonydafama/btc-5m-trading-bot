"""V2 newnew A (son-btc-2legs-v2-newnew-a) live-prep truth — never arms CLOB from Desk."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

NA_BOT_ID = "son-btc-2legs-v2-newnew-a"
NA_DISPLAY = "V2 newnew A · abort"
NA_PAPER_UNIT = "btc-son-btc-2legs-v2-newnew-a.service"
NA_LIVE_UNIT = "dual-leg-live-v2-newnew-a.service"
NA_LIVE_PREP_ENV = "V2_NEWNEW_LIVE_ARMED_PREP"
NA_LIVE_ENV = "config/v2_newnew_live_exec.env"


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def _read_dotenv_key(root: Path, key: str) -> str | None:
    path = root / ".env"
    if not path.is_file():
        return None
    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return None


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
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _unit_state(unit: str) -> tuple[str, bool]:
    base = unit[:-8] if unit.endswith(".service") else unit
    try:
        active = (
            subprocess.run(
                ["systemctl", "is-active", base],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout.strip()
            == "active"
        )
        enabled = (
            subprocess.run(
                ["systemctl", "is-enabled", base],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout.strip()
            in ("enabled", "static")
        )
        return ("active" if active else "inactive"), enabled
    except (OSError, subprocess.SubprocessError):
        return "unknown", False


def _live_env(root: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = root / NA_LIVE_ENV
    if not env_path.is_file():
        return env
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _write_live_env_keys(root: Path, updates: dict[str, str]) -> None:
    path = root / NA_LIVE_ENV
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    found: set[str] = set()
    out: list[str] = []
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            out.append(line)
            continue
        key = raw.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            found.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in found:
            out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def live_exec_is_real(root: Path) -> bool:
    """True when the NA live env would post real FOK orders."""
    env = _live_env(root)
    if _truthy(env.get("DRY_RUN", "1")) or _truthy(env.get("DUAL_LEG_LIVE_DRY_RUN", "1")):
        return False
    if not _truthy(env.get("REAL_MONEY")):
        return False
    return str(env.get("TRADING_MODE") or "").lower() == "live"


def live_armed_prep(root: Path) -> bool:
    return _truthy(_read_dotenv_key(root, NA_LIVE_PREP_ENV))


def live_active(root: Path) -> bool:
    return live_exec_is_real(root) and _unit_state(NA_LIVE_UNIT)[0] == "active"


def _registry_meta(root: Path) -> dict[str, Any]:
    path = root / "configs" / "sons" / "registry.json"
    if not path.is_file():
        return {}
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    for row in reg.get("sons") or []:
        if isinstance(row, dict) and str(row.get("id")) == NA_BOT_ID:
            return row
    return {}


def live_control_payload(root: Path, *, wallet_cash_usdc: float | None = None) -> dict[str, Any]:
    meta = _registry_meta(root)
    armed = live_armed_prep(root)
    active = live_active(root)
    live_state, live_enabled = _unit_state(NA_LIVE_UNIT)
    paper_state, _ = _unit_state(NA_PAPER_UNIT)
    placeholder = not live_exec_is_real(root)
    min_wallet = 100.0
    wallet = float(wallet_cash_usdc) if wallet_cash_usdc is not None else None
    wallet_ok = wallet is not None and wallet >= min_wallet
    shared_wallet_note = (
        "Polymarket 錢包與 Son U 真倉共用；NA 真 go-live 前需加錢或暫停 Son U。"
    )
    if active:
        label_tc = "而家模式：Live（真倉 ON）"
    elif armed:
        label_tc = "Live預備 · 紙上 armed · 真倉 OFF（live unit disabled）"
    else:
        label_tc = "Live預備 · 紙上跑緊 · 真倉 OFF"
    return {
        "ok": True,
        "son_id": NA_BOT_ID,
        "display_name": NA_DISPLAY,
        "mode": "live" if active else "paper",
        "paper_active": paper_state == "active",
        "live_active": active,
        "live_armed": armed and not active,
        "live_armed_prep": armed and placeholder,
        "exec_is_real_live": live_exec_is_real(root),
        "exec_dry_run_placeholder": not live_exec_is_real(root),
        "live_unit": NA_LIVE_UNIT,
        "live_unit_state": live_state,
        "live_unit_enabled": live_enabled,
        "paper_unit": NA_PAPER_UNIT,
        "paper_unit_state": paper_state,
        "go_live_ready": bool(meta.get("go_live_ready")),
        "live_tag": meta.get("live_tag") or "live-prep",
        "status": meta.get("status") or "RUNNING · paper",
        "toggle_target": "live" if not active else "paper",
        "wallet_cash_usdc": wallet_cash_usdc,
        "wallet_min_usdc": min_wallet,
        "wallet_ok_for_live": wallet_ok,
        "wallet_shared_with_son_u": True,
        "to_live_blocked_reason": None if active or live_exec_is_real(root) else None,
        "label_tc": label_tc,
        "note_tc": shared_wallet_note,
        "knobs": meta.get("knobs") or {},
    }


def _systemctl(*args: str) -> None:
    try:
        subprocess.run(["systemctl", *args], check=False, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass


def apply_live_control_action(root: Path, action: str, *, wallet_cash_usdc: float | None = None) -> dict[str, Any]:
    """Dashboard switch. Paper stops the live unit. Live starts it and posts FOK."""
    action = (action or "").strip().lower()
    base = live_control_payload(root, wallet_cash_usdc=wallet_cash_usdc)
    if action == "to_paper":
        _systemctl("disable", "--now", NA_LIVE_UNIT)
        _write_live_env_keys(
            root,
            {
                "TRADING_MODE": "paper",
                "REAL_MONEY": "0",
                "DRY_RUN": "1",
                "DUAL_LEG_LIVE_DRY_RUN": "1",
            },
        )
        _write_dotenv_key(root, NA_LIVE_PREP_ENV, "0")
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", NA_PAPER_UNIT)
        out = live_control_payload(root, wallet_cash_usdc=wallet_cash_usdc)
        out.update(
            {
                "ok": True,
                "action": action,
                "desk_mode": "paper",
                "mode": "paper",
                "message_tc": "V2 newnew A 已切換 → Paper（真倉 OFF）",
            }
        )
        return out
    if action == "to_live":
        if wallet_cash_usdc is not None and float(wallet_cash_usdc) < 100.0:
            return {
                **base,
                "ok": False,
                "action": action,
                "error": "wallet_gate",
                "error_tc": f"銀包 ${float(wallet_cash_usdc):.2f} 未達 $100，唔會開真倉",
            }
        _write_live_env_keys(
            root,
            {
                "TRADING_MODE": "live",
                "REAL_MONEY": "1",
                "DRY_RUN": "0",
                "DUAL_LEG_LIVE_DRY_RUN": "0",
                "DUAL_LEG_LIVE_PROFILE": "v2_live_shadow",
                "DUAL_LEG_LIVE_UNIT_USD": "6",
                "REQUIRE_OPP_BEFORE_LEG1": "1",
                "OPP_BEFORE_LEG1_MAX": "0.50",
            },
        )
        _write_dotenv_key(root, NA_LIVE_PREP_ENV, "1")
        _systemctl("daemon-reload")
        _systemctl("stop", NA_PAPER_UNIT)
        _systemctl("enable", "--now", NA_LIVE_UNIT)
        out = live_control_payload(root, wallet_cash_usdc=wallet_cash_usdc)
        if not out.get("live_active"):
            _write_live_env_keys(
                root,
                {
                    "TRADING_MODE": "paper",
                    "REAL_MONEY": "0",
                    "DRY_RUN": "1",
                    "DUAL_LEG_LIVE_DRY_RUN": "1",
                },
            )
            _systemctl("disable", "--now", NA_LIVE_UNIT)
            _systemctl("enable", "--now", NA_PAPER_UNIT)
            failed = live_control_payload(root, wallet_cash_usdc=wallet_cash_usdc)
            failed.update(
                {
                    "ok": False,
                    "action": action,
                    "error": "live_unit_down",
                    "error_tc": "V2 newnew A live unit 未啟動，已返回 Paper",
                }
            )
            return failed
        out.update(
            {
                "ok": True,
                "action": action,
                "desk_mode": "live",
                "mode": "live",
                "message_tc": "V2 newnew A 已切換 → Live（真倉 ON）",
            }
        )
        return out
    return {**base, "ok": False, "error": "action must be to_live or to_paper", "error_tc": "未知 action"}
