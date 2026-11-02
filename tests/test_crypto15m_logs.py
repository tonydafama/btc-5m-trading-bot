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

    def test_sonu_only_excludes_fleet_bots(self) -> None:
        log = Path(self.tmp) / "logs" / "crypto_15m_trade_log_live.json"
        _write(
            log,
            [
                {"entered": True, "trade_result": "WIN", "bot": "BTC V1", "timestamp_hkt": "2026-01-03 12:00:00"},
                {"entered": True, "trade_result": "LOSS", "bot": "ETH", "timestamp_hkt": "2026-01-03 12:05:00"},
            ],
        )
        import importlib

        importlib.reload(self.eng)
        rows = self.eng.recent_trades(ledger="live", limit=10, sonu_only=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bot"], "ETH")

    def test_window_funnel_in_summary(self) -> None:
        summary = self.eng.crypto_15m_summary()
        wf = summary.get("window_funnel") or {}
        self.assertEqual(wf.get("considered"), 2)
        self.assertEqual(wf.get("filtered"), 1)
        self.assertEqual(wf.get("entered"), 1)
        self.assertEqual(wf.get("trades"), 1)
        self.assertEqual(wf.get("ledger"), "paper")

    def test_enrich_summary_window_funnel(self) -> None:
        bare = {"mode": "paper", "btc": {"trades_entered": 1}, "eth": {"trades_entered": 0}}
        enriched = self.eng.enrich_summary_window_funnel(bare)
        wf = enriched.get("window_funnel") or {}
        self.assertEqual(wf.get("considered"), 2)
        self.assertEqual(wf.get("entered"), 1)
        self.assertEqual(wf.get("ledger"), "paper")


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
        self.assertIn("sonu-logs-link", html)
        self.assertIn("睇 Log → /logs", html)
        self.assertNotIn("c15m-live-trades-body", html)
        self.assertNotIn("c15m-paper-log-fold", html)
        self.assertNotIn("sonu_only=1", html)
        self.assertIn("mode-lamp lamp-green", html)
        self.assertIn("c15m-win-considered", html)
        self.assertIn("考慮", html)
        self.assertIn("sonu-funnel-kicker", html)
        self.assertIn("窗口漏斗", html)
        self.assertIn("c15m-win-filtered", html)
        self.assertIn("入場", html)
        self.assertIn("paper-run-fold", html)
        self.assertIn("c15m-trades-windows-line", html)
        self.assertIn("sonu-trades-line", html)


if __name__ == "__main__":
    unittest.main()
