"""Live decisions use the same checkpoints as the paper observer."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dual_leg_live as live  # noqa: E402


class PaperDecisionClockTests(unittest.TestCase):
    def test_window_open_is_not_a_decision(self) -> None:
        self.assertIsNone(live.paper_decision_checkpoint(299.0, set()))
        self.assertIsNone(live.paper_decision_checkpoint(180.0, set()))
        self.assertIsNone(live.paper_decision_checkpoint(78.0, set()))

    def test_hits_paper_band_once(self) -> None:
        seen: set[int] = set()
        self.assertEqual(live.paper_decision_checkpoint(77.0, seen), 75)
        seen.add(75)
        self.assertIsNone(live.paper_decision_checkpoint(74.0, seen))
        self.assertEqual(live.paper_decision_checkpoint(62.0, seen), 60)
        seen.add(60)
        self.assertEqual(live.paper_decision_checkpoint(50.0, seen), 50)
        seen.add(50)
        self.assertEqual(live.paper_decision_checkpoint(40.0, seen), 40)
        seen.add(40)
        self.assertEqual(live.paper_decision_checkpoint(30.0, seen), 30)
        seen.add(30)
        # 27 is inside both T-30 and T-25. T-30 already fired, so this is T-25.
        self.assertEqual(live.paper_decision_checkpoint(27.0, seen), 25)
        seen.add(25)
        self.assertEqual(live.paper_decision_checkpoint(12.0, seen), 12)
        seen.add(12)
        self.assertEqual(live.paper_decision_checkpoint(5.0, seen), 5)

    def test_band_edges_match_observer(self) -> None:
        # observer: t_rem <= cp+2 and t_rem >= cp-3
        self.assertEqual(live.paper_decision_checkpoint(72.0, set()), 75)
        self.assertIsNone(live.paper_decision_checkpoint(71.9, set()))
        self.assertEqual(live.paper_decision_checkpoint(2.0, set()), 5)
        self.assertIsNone(live.paper_decision_checkpoint(1.9, set()))

    def test_fast_poll_only_inside_the_watch_window(self) -> None:
        self.assertEqual(live.decision_poll_sec(200.0, 8.0), 8.0)
        self.assertEqual(live.decision_poll_sec(80.0, 8.0), live.PAPER_DECISION_POLL_SEC)
        self.assertEqual(live.decision_poll_sec(75.0, 8.0), 1.0)
        self.assertLess(live.PAPER_DECISION_POLL_SEC, 5.0)


if __name__ == "__main__":
    unittest.main()
