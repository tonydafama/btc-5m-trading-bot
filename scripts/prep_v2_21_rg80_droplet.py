#!/usr/bin/env python3
"""Stop losing live book, archive v2-live-20261107-011629, reset ledger, arm V2 21+ Rg80 prep (DRY_RUN only)."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/root/btc-5m-bot")
HKT = ZoneInfo("Asia/Hong_Kong")
LIVE_UNIT = "dual-leg-live-v2.service"
OLD_SESSION = "v2-live-20261107-011629"
STRATEGY = "V2 21+ Rg80"


def _stamp() -> str:
    return datetime.now(HKT).strftime("%Y%m%d-%H%M%S")


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=False, timeout=60)


def main() -> None:
    stamp = _stamp()
    new_session = f"v2-live-{stamp}-rg80-prep"
    arch = ROOT / "logs" / "archive" / f"v2_live_stop_{OLD_SESSION}_{stamp}"
    arch.mkdir(parents=True, exist_ok=True)

    _run(["systemctl", "stop", LIVE_UNIT])
    _run(["systemctl", "disable", LIVE_UNIT])

    for rel in (
        "logs/dual_leg_live_trades.json",
        "logs/dual_leg_live_status.json",
        "logs/dual_leg_live_state.json",
        "logs/dual_leg_v2_live_meta.json",
        "config/dual_leg_v2_live_session.json",
        "logs/dual_leg_live_daemon.jsonl",
    ):
        src = ROOT / rel
        if src.is_file():
            shutil.copy2(src, arch / src.name)

    jl = ROOT / "logs" / "dual_leg_live_daemon.jsonl"
    if jl.is_file():
        shutil.copy2(jl, arch / jl.name)

    reset_evt = {
        "event": "live_display_reset",
        "timestamp_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
        "session_id": new_session,
        "strategy_label": STRATEGY,
        "archived_from": OLD_SESSION,
        "archive_path": str(arch),
    }
    (ROOT / "logs" / "dual_leg_live_daemon.jsonl").write_text(
        json.dumps(reset_evt, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    (ROOT / "logs" / "dual_leg_live_trades.json").write_text("[]\n", encoding="utf-8")
    (ROOT / "logs" / "dual_leg_live_state.json").write_text("{}\n", encoding="utf-8")

    status = {
        "updated_at_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
        "dry_run": True,
        "DUAL_LEG_LIVE_DRY_RUN": "1",
        "live_enabled": False,
        "profile": "v2",
        "logic": "V2",
        "session_id": new_session,
        "strategy_label": STRATEGY,
        "unit_usd": None,
        "posted_live_orders": 0,
        "open_positions": 0,
        "open_slugs": [],
        "samples_total": 0,
        "windows_considered": 0,
        "errors": 0,
        "realized_pnl_usd": 0.0,
        "settled_trades": 0,
        "pending_settle_trades": 0,
        "decision_clock": "legacy_poll",
        "decision_checkpoints": [],
        "rg80": {"enabled": True, "min_usd": 80.0, "paused": False, "consec_ok": 0},
        "note": f"{STRATEGY} · 新 section · 真倉 OFF · 等 user Go Live",
        "display_epoch_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
    }
    (ROOT / "logs" / "dual_leg_live_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    meta = {
        "session_id": new_session,
        "strategy_label": STRATEGY,
        "realized_pnl_usd": 0.0,
        "settled_trades": 0,
        "archived_from": OLD_SESSION,
        "archive_path": str(arch),
        "note": f"{STRATEGY} prep · DRY_RUN=1 · legacy clock · Rg80",
    }
    (ROOT / "logs" / "dual_leg_v2_live_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    sess = {
        "session_id": new_session,
        "strategy_label": STRATEGY,
        "started_at_hkt": status["display_epoch_hkt"],
        "archived_previous": OLD_SESSION,
        "archive_path": str(arch),
        "mode": "prep",
        "dry_run": True,
    }
    (ROOT / "config" / "dual_leg_v2_live_session.json").write_text(
        json.dumps(sess, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    display_epoch = {
        "session_id": new_session,
        "started_at_hkt": status["display_epoch_hkt"],
        "considered_baseline": 0,
        "entered_baseline": 0,
    }
    (ROOT / "logs" / "dual_leg_live_display_epoch.json").write_text(
        json.dumps(display_epoch, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    env_path = ROOT / "config" / "v2_parent_live_exec.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    dotenv = ROOT / ".env"
    if dotenv.is_file():
        text = dotenv.read_text(encoding="utf-8")
        if "V2_LIVE_ARMED_PREP=" in text:
            import re

            text = re.sub(r"^V2_LIVE_ARMED_PREP=.*$", "V2_LIVE_ARMED_PREP=1", text, flags=re.M)
        else:
            text = text.rstrip() + "\nV2_LIVE_ARMED_PREP=1\n"
        dotenv.write_text(text, encoding="utf-8")
    else:
        dotenv.write_text("V2_LIVE_ARMED_PREP=1\n", encoding="utf-8")

    env_path.write_text(
        f"""# {STRATEGY} live-prep — DRY_RUN until explicit Go Live + stake size
TRADING_MODE=paper
REAL_MONEY=0
DRY_RUN=1
DUAL_LEG_LIVE_DRY_RUN=1
DUAL_LEG_LIVE_PROFILE=v2
REQUIRE_OPP_BEFORE_LEG1=0
DUAL_LEG_MAX_OPPOSITE_ASK=0.72
MAX_OPP_OVERRIDE=0.72
DUAL_LEG_LIVE_STRATEGY_LABEL={STRATEGY}
DUAL_LEG_LIVE_DECISION_CLOCK=legacy_poll
DUAL_LEG_LIVE_RG80=1
DUAL_LEG_LIVE_RG80_MIN_USD=80
V2_LIVE_ARMED_PREP=1
DUAL_LEG_LIVE_SESSION_ID={new_session}
# UNIT unset — user picks low stake at Go Live
""",
        encoding="utf-8",
    )

    # Dashboard only — never enable live unit here
    _run(["systemctl", "restart", "btc-5m-dashboard.service"])

    proc = subprocess.run(
        ["systemctl", "is-active", LIVE_UNIT],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    live_active = proc.stdout.strip() == "active"

    print(
        json.dumps(
            {
                "ok": True,
                "strategy": STRATEGY,
                "new_session_id": new_session,
                "archive_path": str(arch),
                "live_unit_active": live_active,
                "exec_env": str(env_path),
                "went_live": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
