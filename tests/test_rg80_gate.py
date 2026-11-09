"""Rg80Gate: pause below threshold, resume after two windows."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from dual_leg_live import Rg80Gate


class TestRg80Gate(unittest.TestCase):
    @mock.patch.dict(os.environ, {"DUAL_LEG_LIVE_RG80": "1"})
    def test_pause_and_two_window_resume(self) -> None:
        g = Rg80Gate(min_usd=80.0)
        self.assertFalse(g.blocks_new_entry(120.0))
        self.assertTrue(g.blocks_new_entry(50.0))
        self.assertTrue(g.paused)
        g.on_new_window()
        g.blocks_new_entry(90.0)
        self.assertEqual(g.consec_ok, 1)
        self.assertTrue(g.paused)
        g.on_new_window()
        g.blocks_new_entry(85.0)
        self.assertFalse(g.paused)
        self.assertFalse(g.blocks_new_entry(200.0))


if __name__ == "__main__":
    unittest.main()
