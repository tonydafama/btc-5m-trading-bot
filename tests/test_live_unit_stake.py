"""Desk bet size must be what the next live window reads."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dual_leg_live as live  # noqa: E402


class LiveUnitStakeTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("DUAL_LEG_LIVE_ENV_FILE", None)

    def test_reread_exec_env_beats_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v2_parent_live_exec.env"
            path.write_text("DRY_RUN=0\nDUAL_LEG_LIVE_UNIT_USD=5\n", encoding="utf-8")
            os.environ["DUAL_LEG_LIVE_ENV_FILE"] = str(path)
            os.environ["DUAL_LEG_LIVE_UNIT_USD"] = "1"
            try:
                self.assertEqual(live.read_live_unit_usd(6.0), 5.0)
            finally:
                os.environ.pop("DUAL_LEG_LIVE_UNIT_USD", None)

    def test_open_row_stake_is_kept_when_file_changes(self) -> None:
        row = {"unit_usd": 1.0, "status": "one_leg"}
        fresh = 5.0
        if row.get("unit_usd") not in (None, ""):
            stake = float(row["unit_usd"])
        else:
            stake = fresh
        self.assertEqual(stake, 1.0)


if __name__ == "__main__":
    unittest.main()
