#!/usr/bin/env python3
"""Archive midrange-vol logs and start a fresh dashboard session."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from midrange_vol_engine import ensure_session, log_dir  # noqa: E402

HKT = ZoneInfo("Asia/Hong_Kong")


def main() -> None:
    stamp = datetime.now(HKT).strftime("%Y%m%d-%H%M%S")
    arch = log_dir() / "archive" / f"midvol-{stamp}"
    arch.mkdir(parents=True, exist_ok=True)
    for name in (
        "midrange_vol_session.json",
        "midrange_vol_trades.json",
        "midrange_vol_events.jsonl",
        "midrange_vol_daemon_state.json",
    ):
        src = log_dir() / name
        if src.is_file():
            shutil.move(str(src), str(arch / name))
    readme = {
        "archived_at_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
        "note": "Midrange-vol dashboard reset",
    }
    (arch / "README.json").write_text(json.dumps(readme, indent=2) + "\n", encoding="utf-8")
    ensure_session(reset=True)
    print("OK archived to", arch)
    print("OK new session", (log_dir() / "midrange_vol_session.json").read_text(encoding="utf-8")[:120])


if __name__ == "__main__":
    main()
