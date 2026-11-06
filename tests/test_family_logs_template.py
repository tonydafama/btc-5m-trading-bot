"""family_logs.html must keep core log helpers (regression guard)."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAMILY = ROOT / "dashboard" / "templates" / "family_logs.html"


class FamilyLogsTemplateTests(unittest.TestCase):
    def test_core_helpers_present(self) -> None:
        html = FAMILY.read_text(encoding="utf-8")
        self.assertIn("function parseLine(", html)
        self.assertIn("function statusTag(", html)
        self.assertIn("function renderRows(", html)
        self.assertIn("Do NOT delete parseLine / statusTag / renderRows", html)

    def test_parent_v2_logs_ui(self) -> None:
        html = FAMILY.read_text(encoding="utf-8")
        self.assertIn("Log · BTC 2 legs V2", html)
        self.assertIn("LIVE ON · BTC 2 legs V2", html)
        self.assertIn("paper-fold", html)
        self.assertIn("暫無成交事件", html)
        self.assertIn("isFillRow", html)
        self.assertIn("/api/family-bots", html)
        self.assertIn('data-live-mode="all"', html)
        self.assertIn("let liveMode = 'all'", html)
        self.assertIn("/api/dual-leg-log-tail?", html)
        self.assertIn("dual-leg-live-v2", html)
        self.assertIn("function normalizeTailStats(", html)
        self.assertNotIn("sonu_only", html)
        self.assertNotIn("son-btc-5m-ultimate-live", html)
        self.assertNotIn("LOCKED · Live", html)


if __name__ == "__main__":
    unittest.main()
