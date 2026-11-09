#!/usr/bin/env python3
"""Go-live V2 21+ Rg80: snapshot 本金, $3/leg, fix override, start dual-leg-live-v2."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/root/btc-5m-bot")
HKT = ZoneInfo("Asia/Hong_Kong")
SESSION = "v2-live-20261108-094439-rg80-prep"
STRATEGY = "V2 21+ Rg80"
UNIT = 3.0
OVERRIDE = Path("/etc/systemd/system/dual-leg-live-v2.service.d/override.conf")
LIVE_UNIT = "dual-leg-live-v2.service"


def _now_hkt() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def fetch_clob_cash() -> dict:
    w = Path("/root/weather-bot")
    site = w / "venv/lib/python3.12/site-packages"
    for entry in (w, site, ROOT):
        if entry.is_dir() and str(entry) not in sys.path:
            sys.path.insert(0, str(entry))
    from polymarket_api import PolymarketClient

    data = PolymarketClient().fetch_wallet_status()
    if not isinstance(data, dict):
        raise RuntimeError("wallet fetch failed")
    cash = float(data["cash_usdc"])
    return {"cash_usdc": round(cash, 2), "funder": data.get("funder"), "raw": data}


def write_desk_bet() -> None:
    path = ROOT / "logs" / "desk_bot_settings.json"
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data["dual-leg-live-v2"] = {
        "bet_size": UNIT,
        "updated_at_hkt": _now_hkt(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_exec_env() -> None:
    env = f"""# {STRATEGY} — LIVE section {SESSION}
TRADING_MODE=live
REAL_MONEY=1
DRY_RUN=0
DUAL_LEG_LIVE_DRY_RUN=0
DUAL_LEG_LIVE_PROFILE=v2
REQUIRE_OPP_BEFORE_LEG1=0
DUAL_LEG_MAX_OPPOSITE_ASK=0.72
MAX_OPP_OVERRIDE=0.72
DUAL_LEG_LIVE_STRATEGY_LABEL={STRATEGY}
DUAL_LEG_LIVE_DECISION_CLOCK=legacy_poll
DUAL_LEG_LIVE_RG80=1
DUAL_LEG_LIVE_RG80_MIN_USD=80
V2_LIVE_ARMED_PREP=0
DUAL_LEG_LIVE_SESSION_ID={SESSION}
DUAL_LEG_LIVE_UNIT_USD={int(UNIT)}
DUAL_LEG_LIVE_ENV_FILE=/root/btc-5m-bot/config/v2_parent_live_exec.env
"""
    (ROOT / "config" / "v2_parent_live_exec.env").write_text(env, encoding="utf-8")
    dot = ROOT / ".env"
    lines = dot.read_text(encoding="utf-8").splitlines() if dot.is_file() else []

    def upsert(key: str, val: str) -> None:
        nonlocal lines
        found = False
        out: list[str] = []
        for line in lines:
            if line.strip().startswith(f"{key}="):
                out.append(f"{key}={val}")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"{key}={val}")
        lines = out

    upsert("V2_LIVE_ARMED_PREP", "0")
    upsert("V2_BET_SIZE", str(int(UNIT)))
    upsert("V2_STAKE_USD", str(int(UNIT * 2)))
    upsert("DUAL_LEG_LIVE_UNIT_USD", str(int(UNIT)))
    upsert("DUAL_LEG_UNIT_USD", str(int(UNIT)))
    upsert("DUAL_LEG_TRADING_MODE", "live")
    dot.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_override() -> None:
    OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDE.write_text(
        f"""[Service]
Environment=
Environment=PYTHONUNBUFFERED=1
Environment=DUAL_LEG_LIVE_PROFILE=v2
Environment=DUAL_LEG_LIVE_ENTRY_PAUSE=0
Environment=DUAL_LEG_LIVE_UNIT_USD={int(UNIT)}
Environment=DUAL_LEG_LIVE_ENV_FILE=/root/btc-5m-bot/config/v2_parent_live_exec.env
Environment=DUAL_LEG_LIVE_DRY_RUN=0
Environment=REAL_MONEY=1
Environment=DRY_RUN=0
Environment=TRADING_MODE=live
Environment=REQUIRE_OPP_BEFORE_LEG1=0
Environment=DUAL_LEG_LIVE_SESSION_ID={SESSION}
""",
        encoding="utf-8",
    )


def patch_status(cash: float) -> None:
    status_path = ROOT / "logs" / "dual_leg_live_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
    status.update(
        {
            "updated_at_hkt": _now_hkt(),
            "session_id": SESSION,
            "strategy_label": STRATEGY,
            "section_start_capital_usd": cash,
            "section_start_at_hkt": _now_hkt(),
            "unit_usd": UNIT,
            "dry_run": False,
            "DUAL_LEG_LIVE_DRY_RUN": "0",
            "live_enabled": True,
        }
    )
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for rel in ("config/dual_leg_v2_live_session.json", "logs/dual_leg_v2_live_meta.json"):
        p = ROOT / rel
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        data["session_id"] = SESSION
        data["strategy_label"] = STRATEGY
        data["section_start_capital_usd"] = cash
        data["section_start_at_hkt"] = _now_hkt()
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    wallet = fetch_clob_cash()
    cash = float(wallet["cash_usdc"])
    write_desk_bet()
    write_exec_env()
    write_override()
    patch_status(cash)
    subprocess.run(["systemctl", "daemon-reload"], check=False, timeout=30)
    subprocess.run(["systemctl", "enable", "--now", LIVE_UNIT], check=False, timeout=30)
    subprocess.run(["systemctl", "restart", "btc-5m-dashboard.service"], check=False, timeout=30)
    proc = subprocess.run(
        ["systemctl", "is-active", LIVE_UNIT],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    print(
        json.dumps(
            {
                "ok": proc.stdout.strip() == "active",
                "session_id": SESSION,
                "unit_usd": UNIT,
                "section_start_capital_usd": cash,
                "funder": wallet.get("funder"),
                "live_unit": proc.stdout.strip(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
