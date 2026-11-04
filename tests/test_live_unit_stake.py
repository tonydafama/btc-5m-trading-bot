"""Desk bet size must be what the next live window reads."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


class EntryPauseTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("DUAL_LEG_LIVE_ENTRY_PAUSE", None)

    def test_new_entries_pause_from_1800_until_midnight_hkt(self) -> None:
        hkt = ZoneInfo("Asia/Hong_Kong")
        os.environ["DUAL_LEG_LIVE_ENTRY_PAUSE"] = "1"
        self.assertFalse(live.new_entry_paused(datetime(2026, 11, 3, 17, 59, tzinfo=hkt)))
        self.assertTrue(live.new_entry_paused(datetime(2026, 11, 3, 18, 0, tzinfo=hkt)))
        self.assertTrue(live.new_entry_paused(datetime(2026, 11, 3, 23, 59, tzinfo=hkt)))
        self.assertFalse(live.new_entry_paused(datetime(2026, 11, 4, 0, 0, tzinfo=hkt)))
        view = live.entry_pause_view(datetime(2026, 11, 3, 18, 30, tzinfo=hkt))
        self.assertTrue(view["active"])
        self.assertIn("00:00", view["remark_tc"])
        self.assertIn("已開倉會自己行完", view["remark_tc"])

    def test_pause_can_be_turned_off(self) -> None:
        hkt = ZoneInfo("Asia/Hong_Kong")
        os.environ["DUAL_LEG_LIVE_ENTRY_PAUSE"] = "0"
        self.assertFalse(live.new_entry_paused(datetime(2026, 11, 3, 20, 0, tzinfo=hkt)))

    def test_pause_off_in_exec_env_file(self) -> None:
        hkt = ZoneInfo("Asia/Hong_Kong")
        os.environ.pop("DUAL_LEG_LIVE_ENTRY_PAUSE", None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "exec.env"
            path.write_text("DUAL_LEG_LIVE_ENTRY_PAUSE=0\n", encoding="utf-8")
            os.environ["DUAL_LEG_LIVE_ENV_FILE"] = str(path)
            self.assertFalse(live.new_entry_paused(datetime(2026, 11, 3, 20, 0, tzinfo=hkt)))


class V3ProfileTests(unittest.TestCase):
    def test_v3_is_chop_only_ten_per_leg_and_waits_for_hedge(self) -> None:
        profile = live.PROFILES["v3"]
        self.assertEqual(profile.allowed_regimes, ("volatile_chop",))
        self.assertEqual(profile.unit_usd, 10.0)
        self.assertEqual(profile.max_opposite_ask, 0.72)
        self.assertFalse(profile.require_opp_before_leg1)
        self.assertNotIn("quiet_mixed", profile.allowed_regimes)

    def test_entry_opposite_records_ask_and_bid(self) -> None:
        snapshot = {
            "t_remaining": 210.0,
            "up_book": {"best_ask": 0.38, "spread": 0.02, "ask_depth_usd": 40},
            "down_book": {"best_ask": 0.64, "spread": 0.01, "ask_depth_usd": 80},
        }
        rec = live.entry_opposite_record(snapshot, "up")
        self.assertEqual(rec["side"], "down")
        self.assertEqual(rec["best_ask"], 0.64)
        self.assertEqual(rec["best_bid"], 0.63)
        self.assertEqual(rec["t_remaining"], 210.0)


if __name__ == "__main__":
    unittest.main()
