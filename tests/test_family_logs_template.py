"""family_logs.html must keep core log helpers (Son U lock regression guard)."""

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

    def test_revamped_sonu_logs_ui(self) -> None:
        html = FAMILY.read_text(encoding="utf-8")
        self.assertIn("Log · Son U", html)
        self.assertIn("終極5m · Live", html)
        self.assertIn("sonu-paper-fold", html)
        self.assertIn("sonu_only=1", html)
        self.assertIn("isSonuTradeRow", html)
        self.assertIn("暫無成交", html)
        self.assertIn("isFillRow", html)
        self.assertIn("/api/family-bots", html)
        self.assertIn("data-live-mode=\"all\"", html)
        self.assertIn("let liveMode = 'all'", html)
        self.assertIn("/api/bot-log-tail?id=", html)
        self.assertNotIn("LOCKED · Live", html)
        self.assertNotIn("id=son-btc", html)


if __name__ == "__main__":
    unittest.main()
