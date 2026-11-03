"""Paper and Live ledgers stay separate for the three gold-box bots."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import mode_books as mb  # noqa: E402


def _dump(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")


class ModeBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mode-books-"))
        mb._ROW_CACHE.clear()

    def test_skips_are_not_wins_and_modes_split(self) -> None:
        _dump(
            self.root / "logs" / "sons" / "son-btc-5m-ultimate-live_trades.json",
            [
                {"slug": "a", "mode": "PAPER", "trade_result": "SKIP"},
                {"slug": "b", "mode": "PAPER", "trade_result": "WIN", "pnl": 4},
                {"slug": "c", "mode": "LIVE", "trade_result": "LOSS", "pnl": -2.5},
            ],
        )
        books = mb.books_for_bot(self.root, mb.SONU_ID)
        paper = books["paper"]["rows"][0]
        live = books["live"]["rows"][0]
        self.assertEqual(paper["scope"], "今次")
        self.assertEqual((paper["wins"], paper["losses"], paper["pnl"]), (1, 0, 4.0))
        self.assertEqual(paper["wr_label"], "100%")
        self.assertEqual(paper["skips"], 1)
        self.assertEqual((live["wins"], live["losses"], live["pnl"]), (0, 1, -2.5))
        self.assertEqual(live["wr_label"], "0%")

    def test_parent_live_dedupes_archives_and_drops_other_profiles(self) -> None:
        _dump(
            self.root / "logs" / "dual_leg_btc_paper_trades.json",
            [
                {"slug": "p1", "mode": "paper", "session_id": "paper-v2-100", "trade_result": "WIN", "pnl": 3},
                {"slug": "p2", "mode": "paper", "session_id": "paper-v2-100", "trade_result": "LOSS", "pnl": -1},
            ],
        )
        _dump(self.root / "logs" / "dual_leg_live_trades.json", [])
        _dump(
            self.root / "logs" / "archive" / "parent_v2_golive_20261027-202517" / "dual_leg_live_trades.json",
            [
                {"slug": "L1", "mode": "LIVE", "profile": "v2", "trade_result": "WIN", "pnl": 2},
                {"slug": "L2", "mode": "LIVE", "profile": "v2", "trade_result": "BREAKEVEN", "pnl": 0},
            ],
        )
        _dump(
            self.root / "logs" / "archive" / "copy" / "dual_leg_live_trades.json",
            [{"slug": "L1", "mode": "LIVE", "profile": "v2", "trade_result": "WIN", "pnl": 2}],
        )
        _dump(
            self.root / "logs" / "archive" / "dual_leg_v1_live_pre" / "dual_leg_live_trades.json",
            [{"slug": "old", "mode": "LIVE", "profile": "v1", "trade_result": "LOSS", "pnl": -40}],
        )
        _dump(
            self.root / "logs" / "archive" / "shadow" / "dual_leg_live_trades.json",
            [{"slug": "na", "mode": "LIVE", "profile": "v2_live_shadow", "trade_result": "WIN", "pnl": 9}],
        )
        books = mb.books_for_bot(self.root, mb.V2_ID)
        paper = books["paper"]["rows"][0]
        live = books["live"]["rows"][0]
        self.assertEqual(paper["wr_label"], "50%")
        self.assertEqual(paper["pnl"], 2.0)
        self.assertEqual(live["scope"], "歷史")
        self.assertEqual((live["wins"], live["losses"], live["breakeven"]), (1, 0, 1))
        self.assertEqual(live["pnl"], 2.0)
        self.assertEqual(live["wr_label"], "100%")
        self.assertIn("1 段", live["note"])
        self.assertIn("今次帳 0 單", live["note"])

        na = mb.books_for_bot(self.root, mb.NA_ID)
        na_live = na["live"]["rows"][0]
        self.assertEqual(na_live["wins"], 1)
        self.assertEqual(na_live["pnl"], 9.0)
        self.assertEqual(na_live["scope"], "歷史")

    def test_prior_paper_skips_a_newer_skip_only_reset(self) -> None:
        _dump(
            self.root / "logs" / "sons" / "son-btc-5m-ultimate-live_trades.json",
            [{"slug": "now", "mode": "PAPER", "trade_result": "SKIP"}],
        )
        newer = self.root / "logs" / "archive" / "reset-new" / "son-btc-5m-ultimate-live_trades.json"
        older = self.root / "logs" / "archive" / "reset-old" / "son-btc-5m-ultimate-live_trades.json"
        _dump(newer, [{"slug": "s", "mode": "PAPER", "trade_result": "SKIP"}])
        _dump(
            older,
            [
                {"slug": "w", "mode": "PAPER", "trade_result": "WIN", "pnl": 1},
                {"slug": "l1", "mode": "PAPER", "trade_result": "LOSS", "pnl": -3},
                {"slug": "l2", "mode": "PAPER", "trade_result": "LOSS", "pnl": -5},
            ],
        )
        newer.touch()
        books = mb.books_for_bot(self.root, mb.SONU_ID)
        self.assertEqual(len(books["paper"]["rows"]), 2)
        prior = books["paper"]["rows"][1]
        self.assertEqual(prior["scope"], "上次")
        self.assertEqual((prior["wins"], prior["losses"], prior["pnl"]), (1, 2, -7.0))
        self.assertEqual(prior["wr_label"], "33.3%")

    def test_attach_headline_follows_the_active_mode(self) -> None:
        _dump(
            self.root / "logs" / "dual_leg_btc_paper_trades.json",
            [{"slug": "p", "mode": "paper", "session_id": "paper-v2", "trade_result": "WIN", "pnl": 1.5}],
        )
        _dump(
            self.root / "logs" / "archive" / "live" / "dual_leg_live_trades.json",
            [{"slug": "L", "mode": "LIVE", "profile": "v2", "trade_result": "LOSS", "pnl": -4}],
        )
        row = {"id": mb.V2_ID, "live_enabled": False, "wr_label": "曾live", "pnl": None}
        mb.attach_mode_books(self.root, [row])
        self.assertEqual(row["wr_label"], "100%")
        self.assertEqual(row["pnl_label"], "+$1.50")
        self.assertEqual(row["mode_books"]["live"]["rows"][0]["pnl"], -4.0)


if __name__ == "__main__":
    unittest.main()
