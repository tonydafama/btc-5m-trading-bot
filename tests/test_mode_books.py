"""Paper and Live ledgers stay separate for the three gold-box bots."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

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
        os.environ["MODE_BOOKS_NO_SYSTEMD"] = "1"
        self._now = mb._now_hkt
        mb._now_hkt = lambda: datetime(2026, 11, 3, 16, 0)

    def tearDown(self) -> None:
        mb._now_hkt = self._now
        if hasattr(self, "_unit_since"):
            mb._unit_active_since_hkt = self._unit_since

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
        live = next(row for row in books["live"]["rows"] if row["scope"] == "歷史")
        self.assertEqual(books["live"]["rows"][0]["scope"], "今次")
        self.assertEqual(books["live"]["when_label"], "今次未開始")
        self.assertEqual(paper["wr_label"], "50%")
        self.assertEqual(paper["pnl"], 2.0)
        self.assertEqual((live["wins"], live["losses"], live["breakeven"]), (1, 0, 1))
        self.assertEqual(live["pnl"], 2.0)
        self.assertEqual(live["wr_label"], "100%")
        self.assertIn("1 段", live["note"])

        na = mb.books_for_bot(self.root, mb.NA_ID)
        na_live = next(row for row in na["live"]["rows"] if row["scope"] == "歷史")
        self.assertEqual(na_live["wins"], 1)
        self.assertEqual(na_live["pnl"], 9.0)
        self.assertEqual(na["live"]["when_label"], "今次未開始")

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

    def test_open_empty_v2_paper_stays_at_zero(self) -> None:
        from datetime import datetime

        _dump(self.root / "logs" / "dual_leg_btc_paper_trades.json", [])
        archive = self.root / "logs" / "archive" / "btc_v2_paper" / "dual_leg_btc_paper_trades.json"
        _dump(
            archive,
            [{"slug": "old", "mode": "paper", "session_id": "paper-v2", "trade_result": "WIN", "pnl": 587.6}],
        )
        started = datetime(2026, 11, 7, 1, 8, 27)
        with mock.patch.object(mb, "_unit_active_since_hkt", return_value=started):
            row = {"id": mb.V2_ID, "live_enabled": False}
            mb.attach_mode_books(self.root, [row])
        self.assertEqual(row["pnl"], 0.0)
        self.assertEqual(row["settled_n"], 0)
        scopes = [line["scope"] for line in row["mode_books"]["paper"]["rows"]]
        self.assertEqual(scopes, ["今次", "上次"])
        self.assertTrue(row["mode_books"]["paper"]["rows"][0]["open"])

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
        hist = next(item for item in row["mode_books"]["live"]["rows"] if item["scope"] == "歷史")
        self.assertEqual(hist["pnl"], -4.0)
        self.assertEqual(row["mode_books"]["live"]["when_label"], "今次未開始")

    def test_live_duration_adds_separate_sessions(self) -> None:
        rows = [
            {
                "slug": "a",
                "mode": "LIVE",
                "opened_at_hkt": "2026-10-24 13:27:33 HKT",
                "settled_at_hkt": "2026-10-24 18:54:03 HKT",
                "trade_result": "WIN",
                "pnl": 1,
            },
            {
                "slug": "b",
                "mode": "LIVE",
                "opened_at_hkt": "2026-11-01 17:30:28 HKT",
                "settled_at_hkt": "2026-11-01 18:50:13 HKT",
                "trade_result": "LOSS",
                "pnl": -1,
            },
        ]
        line = mb._line("歷史", rows, "note")
        self.assertEqual(line["when_label"], "10/24 13:27 → 11/01 18:50")
        self.assertLess(line["duration_min"], 24 * 60)
        self.assertGreater(line["duration_min"], 6 * 60)
        self.assertNotIn("日", line["duration_label"])

    def test_a_running_live_unit_opens_a_new_session_above_history(self) -> None:
        _dump(self.root / "logs" / "dual_leg_live_trades.json", [])
        _dump(
            self.root / "logs" / "archive" / "old" / "dual_leg_live_trades.json",
            [{"slug": "old", "mode": "LIVE", "profile": "v2", "trade_result": "LOSS", "pnl": -1, "opened_at_hkt": "2026-10-24 13:27:00 HKT", "settled_at_hkt": "2026-10-24 14:00:00 HKT"}],
        )
        self._unit_since = mb._unit_active_since_hkt
        mb._unit_active_since_hkt = lambda unit: datetime(2026, 11, 3, 15, 54) if unit == mb.V2_LIVE_UNIT else None
        books = mb.books_for_bot(self.root, mb.V2_ID)
        self.assertEqual(books["live"]["when_label"], "11/03 15:54 → 而家")
        self.assertEqual(books["live"]["rows"][0]["scope"], "今次")
        self.assertEqual(books["live"]["rows"][0]["pnl"], 0.0)
        self.assertEqual(books["live"]["rows"][1]["scope"], "歷史")
        self.assertEqual(books["live"]["rows"][1]["pnl"], -1.0)
        row = {"id": mb.V2_ID, "live_enabled": True}
        mb.attach_mode_books(self.root, [row])
        self.assertEqual(row["pnl"], 0.0)
        self.assertEqual(row["settled_n"], 0)

    def test_v3_live_record_follows_history(self) -> None:
        _dump(
            self.root / "logs" / "dual_leg_live_trades.json",
            [{"slug": "v2", "mode": "LIVE", "profile": "v2", "trade_result": "WIN", "pnl": 5, "opened_at_hkt": "2026-11-03 16:11:00 HKT", "settled_at_hkt": "2026-11-03 16:20:00 HKT"}],
        )
        _dump(
            self.root / "logs" / "archive" / "v3_live_session_20261104-1105" / "dual_leg_live_trades.json",
            [{"slug": "v3", "mode": "LIVE", "profile": "v3", "trade_result": "LOSS", "pnl": -138, "opened_at_hkt": "2026-11-04 11:05:00 HKT", "settled_at_hkt": "2026-11-04 14:50:00 HKT"}],
        )
        _dump(
            self.root / "logs" / "archive" / "older" / "dual_leg_live_trades.json",
            [{"slug": "old", "mode": "LIVE", "profile": "v2", "trade_result": "LOSS", "pnl": -1, "opened_at_hkt": "2026-10-24 13:27:00 HKT", "settled_at_hkt": "2026-10-24 14:00:00 HKT"}],
        )
        books = mb.books_for_bot(self.root, mb.V2_ID)
        scopes = [line["scope"] for line in books["live"]["rows"]]
        self.assertEqual(scopes, ["今次", "歷史", "V3"])
        self.assertEqual(books["live"]["rows"][0]["pnl"], 5.0)
        self.assertNotIn("V3", books["live"]["rows"][0]["note"])
        self.assertEqual(books["live"]["rows"][1]["pnl"], -1.0)
        v3 = books["live"]["rows"][2]
        self.assertEqual(v3["pnl"], -138.0)
        self.assertEqual(v3["when_label"], "11/04 11:05 → 14:50")
        self.assertIn("V3 真倉", v3["note"])
        self.assertEqual(books["live"]["when_label"], "11/03 16:11 → 16:20")

    def test_open_book_runs_until_now(self) -> None:
        line = mb._line(
            "今次",
            [{"slug": "a", "logged_at_hkt": "2026-11-03 10:44:00 HKT", "trade_result": "SKIP"}],
            "note",
            open_book=True,
        )
        self.assertEqual(line["when_label"], "11/03 10:44 → 而家")
        self.assertEqual(line["duration_label"], "5小時16分")

    def test_empty_live_section_has_no_fake_date(self) -> None:
        books = mb.books_for_bot(self.root, mb.NA_ID)
        self.assertEqual(books["live"]["when_label"], "今次未開始")
        self.assertEqual(books["live"]["duration_label"], "—")
        self.assertEqual(books["live"]["rows"][0]["scope"], "今次")

    def test_section_duration_is_the_sum_of_its_books(self) -> None:
        lines = [
            mb._line(
                "上次",
                [{"slug": "old", "logged_at_hkt": "2026-11-02 18:00:00 HKT", "settled_at_hkt": "2026-11-03 07:30:00 HKT", "trade_result": "WIN", "pnl": 1}],
                "prior",
            ),
            mb._line(
                "今次",
                [{"slug": "now", "logged_at_hkt": "2026-11-03 10:44:00 HKT", "trade_result": "SKIP"}],
                "now",
                open_book=True,
            ),
        ]
        section = mb._section("Paper · 紙上", lines, empty_when="未有日期")
        self.assertEqual(section["when_label"], "11/02 18:00 → 而家")
        self.assertGreater(section["rows"][0]["duration_min"], 12 * 60)
        self.assertEqual(section["rows"][1]["duration_label"], "5小時16分")
        self.assertEqual(
            section["duration_label"],
            mb._dur_label(section["rows"][0]["duration_min"] + section["rows"][1]["duration_min"]),
        )

if __name__ == "__main__":
    unittest.main()
