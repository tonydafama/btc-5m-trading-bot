"""Son U live-exec truth + Live↔Paper prep (no fake 真倉 when DRY_RUN placeholder)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

SONU_EXEC_UNIT = "btc-son-btc-5m-ultimate-live-exec.service"
SONU_PAPER_UNIT = "btc-son-btc-5m-ultimate-live.service"
SONU_BOT_ID = "son-btc-5m-ultimate-live"
V2_HIGH_LIVE_ID = "dual-leg-live-v2"
V2_DISPLAY = "BTC 2 legs V2"


def _parse_systemd_environment(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in re.split(r"\s+", (raw or "").strip()):
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip().strip('"')
    return out


def _read_env_file(path: Path, into: dict[str, str]) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        into[k.strip()] = v.strip().strip('"').strip("'")


def read_exec_unit_environment(root: Path) -> dict[str, str]:
    """Merge live-exec unit EnvironmentFiles (later wins) + inline Environment=."""
    found: dict[str, str] = {}
    for path in (
        root / ".env",
        root / "configs" / "sons" / "son-btc-5m-ultimate-live.env",
        root / "config" / "sonu_live_exec.env",
    ):
        _read_env_file(path, found)
    try:
        proc = subprocess.run(
            ["systemctl", "show", SONU_EXEC_UNIT, "-p", "Environment", "--value"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            found.update(_parse_systemd_environment(proc.stdout))
    except (OSError, subprocess.SubprocessError):
        pass
    if found:
        return found
    return {
        "DRY_RUN": os.getenv("SONU_LIVE_EXEC_DRY_RUN", "1"),
        "REAL_MONEY": os.getenv("SONU_LIVE_EXEC_REAL_MONEY", "0"),
        "TRADING_MODE": os.getenv("SONU_LIVE_EXEC_TRADING_MODE", "paper"),
    }


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


def exec_is_real_live(root: Path) -> bool:
    """True only when live-exec would post real FOK (not DRY_RUN placeholder)."""
    env = read_exec_unit_environment(root)
    if _truthy(env.get("DRY_RUN")):
        return False
    if _truthy(env.get("DUAL_LEG_LIVE_DRY_RUN", "1")):
        return False
    if not _truthy(env.get("REAL_MONEY")):
        return False
    mode = str(env.get("TRADING_MODE") or "paper").lower()
    return mode == "live"


def exec_dry_run_placeholder(root: Path) -> bool:
    return not exec_is_real_live(root)


def _read_dotenv_key(root: Path, key: str) -> str | None:
    path = root / ".env"
    if not path.is_file():
        return os.getenv(key)
    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return os.getenv(key)


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


def live_armed_prep(root: Path) -> bool:
    return _truthy(_read_dotenv_key(root, "SONU_LIVE_ARMED_PREP"))


def _unit_enabled(unit: str) -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return proc.stdout.strip() in ("enabled", "static")
    except (OSError, subprocess.SubprocessError):
        return False


def _unit_active(unit: str) -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return proc.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def live_active(root: Path) -> bool:
    """On-air 真倉 only when exec path is real and live-exec unit is running."""
    return exec_is_real_live(root) and _unit_active(SONU_EXEC_UNIT)


def live_control_payload(root: Path, *, wallet_cash_usdc: float | None = None) -> dict[str, Any]:
    placeholder = exec_dry_run_placeholder(root)
    exec_real = exec_is_real_live(root)
    armed = live_armed_prep(root)
    active = live_active(root)
    mode = "live" if active else "paper"
    min_wallet = 100.0
    wallet_ok = wallet_cash_usdc is not None and float(wallet_cash_usdc) >= min_wallet
    if active:
        label_tc = "而家模式：Live（真倉 ON）"
    elif exec_real and _unit_enabled(SONU_EXEC_UNIT) and not active:
        label_tc = "Live exec 已 enable · 等 unit active"
    elif armed and placeholder:
        label_tc = "PREP · 紙上 armed · 真倉路徑未接好（live-exec 仍 DRY_RUN placeholder）"
    elif placeholder:
        label_tc = "而家模式：Paper（紙上／暫停）· 真倉路徑未接好（live-exec 仍 DRY_RUN placeholder）"
    else:
        label_tc = "而家模式：Paper（紙上）"
    return {
        "ok": True,
        "son_id": SONU_BOT_ID,
        "mode": mode,
        "paper_active": not active,
        "live_active": active,
        "live_armed": armed and not active,
        "live_armed_prep": armed and placeholder,
        "exec_is_real_live": exec_real,
        "exec_dry_run_placeholder": placeholder,
        "live_unit": SONU_EXEC_UNIT,
        "live_unit_enabled": _unit_enabled(SONU_EXEC_UNIT),
        "paper_unit": SONU_PAPER_UNIT,
        "status": "RUNNING · paper · go-live READY",
        "toggle_target": "live" if not active else "paper",
        "wallet_cash_usdc": wallet_cash_usdc,
        "wallet_min_usdc": min_wallet,
        "wallet_ok_for_live": wallet_ok,
        "label_tc": label_tc,
    }


def apply_live_control_action(root: Path, action: str, *, wallet_cash_usdc: float | None = None) -> dict[str, Any]:
    action = (action or "").strip().lower()
    base = live_control_payload(root, wallet_cash_usdc=wallet_cash_usdc)
    if action == "to_paper":
        _write_dotenv_key(root, "SONU_LIVE_ARMED_PREP", "0")
        try:
            subprocess.run(["systemctl", "disable", "--now", SONU_EXEC_UNIT], check=False, timeout=8)
            subprocess.run(["systemctl", "enable", "--now", SONU_PAPER_UNIT], check=False, timeout=12)
        except (OSError, subprocess.SubprocessError):
            pass
        out = live_control_payload(root, wallet_cash_usdc=wallet_cash_usdc)
        out.update(
            {
                "ok": True,
                "action": action,
                "message_tc": "已切換 → Paper（清除 armed prep · 真倉 OFF）",
            }
        )
        return out

    if action != "to_live":
        return {**base, "ok": False, "error": "action must be to_live or to_paper", "error_tc": "未知 action"}

    if exec_dry_run_placeholder(root):
        _write_dotenv_key(root, "SONU_LIVE_ARMED_PREP", "1")
        out = live_control_payload(root, wallet_cash_usdc=wallet_cash_usdc)
        out.update(
            {
                "ok": True,
                "action": action,
                "message_tc": "已 armed prep（紙上）· 真倉路徑未接好，唔會開 LIVE ON／唔會 enable live-exec",
                "label_tc": "PREP · 紙上 armed · 真倉未開",
            }
        )
        return out

    if wallet_cash_usdc is not None and float(wallet_cash_usdc) < 100.0:
        return {
            **base,
            "ok": False,
            "action": action,
            "error": "wallet_gate",
            "error_tc": f"銀包 ${float(wallet_cash_usdc):.2f} 未達 ${100:.0f}，唔會開真倉",
        }

    _write_dotenv_key(root, "SONU_LIVE_ARMED_PREP", "1")
    try:
        subprocess.run(["systemctl", "stop", SONU_PAPER_UNIT], check=False, timeout=8)
        subprocess.run(["systemctl", "disable", SONU_PAPER_UNIT], check=False, timeout=8)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        subprocess.run(["systemctl", "enable", "--now", SONU_EXEC_UNIT], check=False, timeout=12)
    except (OSError, subprocess.SubprocessError):
        pass
    out = live_control_payload(root, wallet_cash_usdc=wallet_cash_usdc)
    out.update({"ok": True, "action": action, "message_tc": "已請求 Live 真倉（exec 路徑已非 placeholder）"})
    return out
