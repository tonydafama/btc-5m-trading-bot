"""Son U live-exec truth: placeholder must not read as 真倉 ON."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"
sys.path.insert(0, str(DASHBOARD))

import sonu_live_truth as slt  # noqa: E402


class SonuLiveTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="sonu-truth-")
        self.root = Path(self.tmp)
        (self.root / "config").mkdir(parents=True)
        (self.root / "config" / "sonu_live_exec.env").write_text(
            "DRY_RUN=1\nREAL_MONEY=0\nTRADING_MODE=paper\n",
            encoding="utf-8",
        )
        (self.root / ".env").write_text("SONU_LIVE_ARMED_PREP=0\n", encoding="utf-8")

    def test_placeholder_not_real_live(self) -> None:
        self.assertFalse(slt.exec_is_real_live(self.root))
        self.assertTrue(slt.exec_dry_run_placeholder(self.root))

    def test_to_live_arms_prep_without_real_live(self) -> None:
        out = slt.apply_live_control_action(self.root, "to_live", wallet_cash_usdc=100.52)
        self.assertTrue(out.get("ok"))
        self.assertFalse(out.get("live_active"))
        self.assertTrue(out.get("live_armed_prep"))
        self.assertFalse(out.get("exec_is_real_live"))

    def test_high_live_any_live_false_when_placeholder(self) -> None:
        import paper_board as pb

        board = pb._high_live_board(self.root, {"DUAL_LEG_LIVE_DRY_RUN": "1"}, None)
        self.assertFalse(board["any_live"])
        ids = [r["id"] for r in board["rows"]]
        self.assertIn("son-btc-5m-ultimate-live", ids)
        self.assertIn("dual-leg-live-v2", ids)


if __name__ == "__main__":
    unittest.main()
