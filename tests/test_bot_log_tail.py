"""bot_log_tail trades mode must not return skip rows."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

import bot_log_tail as blt


class BotLogTailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="blt-")
        os.environ["BTC_5M_ROOT"] = self.tmp
        log = Path(self.tmp) / "logs"
        log.mkdir(parents=True)
        rows = [
            {"timestamp_hkt": "2026-11-02 18:00:04 HKT", "trade_result": "SKIPPED", "slug": "btc-updown-5m-1", "entered": False},
            {"timestamp_hkt": "2026-11-02 18:05:04 HKT", "trade_result": "WIN", "slug": "btc-updown-5m-2", "entered": True, "pnl": 1.2},
        ]
        (log / "son-btc-5m-ultimate-live_trades.json").write_text(json.dumps(rows), encoding="utf-8")

    def test_trades_mode_no_skips(self) -> None:
        out = blt.tail_bot_log("son-btc-5m-ultimate-live", mode="trades", limit=50)
        self.assertEqual(out["stats"]["fills"], 1)
        self.assertEqual(out["stats"]["skips"], 1)
        self.assertEqual(len(out["lines"]), 1)
        self.assertEqual(out["lines"][0]["trade_result"], "WIN")

    def test_trades_mode_empty_lines_when_no_fills(self) -> None:
        log = Path(self.tmp) / "logs" / "son-btc-5m-ultimate-live_trades.json"
        log.write_text(
            json.dumps([{"trade_result": "SKIPPED", "entered": False, "slug": "x"}]),
            encoding="utf-8",
        )
        out = blt.tail_bot_log("son-btc-5m-ultimate-live", mode="trades", limit=50)
        self.assertEqual(out["lines"], [])
        self.assertEqual(out["stats"]["fills"], 0)
        self.assertEqual(out["stats"]["skips"], 1)

    def test_seat_label_live_unit(self) -> None:
        label = blt.live_seat_label("son-btc-5m-ultimate-live", {"mode": "paper"})
        self.assertIn("Live 席", label)
        self.assertNotEqual(label.lower(), "paper")


if __name__ == "__main__":
    unittest.main()
