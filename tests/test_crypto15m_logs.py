"""Tests for 15m live/paper log split."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class Crypto15mLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="c15m-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        os.environ["BTC_5M_ROOT"] = self.tmp
        log = Path(self.tmp) / "logs"
        log.mkdir(parents=True)
        _write(
            log / "crypto_15m_trade_log_paper.json",
            [
                {"entered": False, "trade_result": "SKIPPED", "timestamp_hkt": "2026-01-01 10:00:00", "bot": "BTC"},
                {"entered": True, "trade_result": "WIN", "pnl": 1.0, "timestamp_hkt": "2026-01-01 10:05:00", "bot": "BTC", "stake": 5},
            ],
        )
        _write(
            log / "crypto_15m_trade_log_live.json",
            [
                {"entered": True, "trade_result": "LOSS", "pnl": -2.0, "timestamp_hkt": "2026-01-02 11:00:00", "bot": "ETH", "stake": 8},
            ],
        )
        os.environ["CRYPTO_15M_TRADING_MODE"] = "paper"
        import importlib
        import paper_15m_engine as eng

        importlib.reload(eng)
        self.eng = eng

    def test_recent_trades_by_ledger(self) -> None:
        paper = self.eng.recent_trades(ledger="paper", limit=10)
        live = self.eng.recent_trades(ledger="live", limit=10)
        self.assertEqual(len(paper), 2)
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["bot"], "ETH")

    def test_entered_only_filters_skips(self) -> None:
        rows = self.eng.recent_trades(ledger="paper", limit=10, entered_only=True)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["entered"])


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class SonUTemplateTests(unittest.TestCase):
    def test_v1_only_has_sonu_gold_box(self) -> None:
        html = (ROOT / "dashboard" / "templates" / "v1_only.html").read_text(encoding="utf-8")
        self.assertIn("Live · Son U", html)
        self.assertIn("sonu-gold-box", html)
        self.assertIn("c15m-on-air", html)
        self.assertIn("on-air-row", html)
        self.assertIn("LIVE ON", html)
        self.assertIn("c15m-log-panel-live", html)
        self.assertIn("c15m-show-paper-skips", html)
        self.assertIn("mode-lamp lamp-green", html)


if __name__ == "__main__":
    unittest.main()
