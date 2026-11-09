"""Tail helpers for Parent V2 Family Logs (/logs)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"
sys.path.insert(0, str(DASHBOARD))

import family_dual_leg_logs as fdl  # noqa: E402

tail_dual_leg_v2_logs = fdl.tail_dual_leg_v2_logs


class FamilyDualLegLogsTests(unittest.TestCase):
    def test_tail_live_jsonl_newest_order_and_trades_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "logs" / "dual_leg_live_daemon.jsonl"
            log.parent.mkdir(parents=True)
            rows = [
                {"event": "live_daemon_start", "timestamp_hkt": "2026-11-07 10:00:00 HKT"},
                {"event": "entry", "timestamp_hkt": "2026-11-07 10:01:00 HKT", "slug": "btc-updown-5m-a"},
                {"event": "skip_opp_before_leg1", "timestamp_hkt": "2026-11-07 10:02:00 HKT", "slug": "b"},
            ]
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            out = tail_dual_leg_v2_logs(mode="all", ledger="live", limit=10, bot_root=root)
            self.assertEqual(out["lines"][0]["event"], "skip_opp_before_leg1")
            st = out["stats"]
            self.assertEqual(st.get("settles"), 0)
            self.assertEqual(st.get("posts"), 1)
            self.assertEqual(st.get("skips"), 1)
            trades_only = tail_dual_leg_v2_logs(mode="trades", ledger="live", limit=10, bot_root=root)
            self.assertEqual(len(trades_only["lines"]), 1)
            self.assertEqual(trades_only["lines"][0]["slug"], "btc-updown-5m-a")

    def test_live_filters_by_session_id_in_kv(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config").mkdir(parents=True)
            (root / "config" / "v2_parent_live_exec.env").write_text(
                "DUAL_LEG_LIVE_SESSION_ID=sess-new\n", encoding="utf-8"
            )
            log = root / "logs" / "dual_leg_live_daemon.jsonl"
            log.parent.mkdir(parents=True)
            rows = [
                {"event": "live_daemon_start", "timestamp_hkt": "2026-11-07 09:00:00 HKT", "session_id": "sess-old"},
                {"event": "entry", "timestamp_hkt": "2026-11-07 09:01:00 HKT", "slug": "a", "session_id": "sess-old"},
                {"event": "live_daemon_start", "timestamp_hkt": "2026-11-07 10:00:00 HKT", "session_id": "sess-new"},
                {"event": "entry", "timestamp_hkt": "2026-11-07 10:01:00 HKT", "slug": "b", "session_id": "sess-new"},
            ]
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            out = tail_dual_leg_v2_logs(mode="all", ledger="live", limit=10, bot_root=root)
            self.assertEqual(out.get("session_id"), "sess-new")
            slugs = [r.get("slug") for r in out["lines"]]
            self.assertIn("b", slugs)
            self.assertNotIn("a", slugs)

    def test_live_legacy_slice_after_last_start_when_no_session_tag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config").mkdir(parents=True)
            (root / "config" / "v2_parent_live_exec.env").write_text("", encoding="utf-8")
            log = root / "logs" / "dual_leg_live_daemon.jsonl"
            log.parent.mkdir(parents=True)
            rows = [
                {"event": "entry", "timestamp_hkt": "2026-11-07 09:00:00 HKT", "slug": "old"},
                {"event": "live_daemon_start", "timestamp_hkt": "2026-11-07 10:00:00 HKT"},
                {"event": "entry", "timestamp_hkt": "2026-11-07 10:01:00 HKT", "slug": "new"},
            ]
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            out = tail_dual_leg_v2_logs(mode="all", ledger="live", limit=10, bot_root=root)
            slugs = [r.get("slug") for r in out["lines"]]
            self.assertIn("new", slugs)
            self.assertNotIn("old", slugs)

    def test_live_stats_settled_vs_posts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "logs" / "dual_leg_live_daemon.jsonl"
            log.parent.mkdir(parents=True)
            rows = [
                {"event": "entry", "timestamp_hkt": "2026-11-07 10:01:00 HKT", "slug": "a"},
                {"event": "hedge", "timestamp_hkt": "2026-11-07 10:02:00 HKT", "slug": "a"},
                {
                    "event": "trade_settled",
                    "timestamp_hkt": "2026-11-07 10:05:00 HKT",
                    "slug": "a",
                    "pnl": -1.5,
                    "outcome": "down",
                    "trade_result": "LOSS",
                },
            ]
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            out = tail_dual_leg_v2_logs(mode="all", ledger="live", limit=10, bot_root=root)
            st = out["stats"]
            self.assertEqual(st.get("settles"), 1)
            self.assertEqual(st.get("posts"), 2)
            self.assertEqual(st.get("skips"), 0)
            settled_line = next(x for x in out["lines"] if x.get("event") == "trade_settled")
            self.assertIn("PnL", settled_line.get("reason") or "")

    def test_tail_paper_trades_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "logs" / "dual_leg_btc_paper_trades.json"
            path.parent.mkdir(parents=True)
            sess = root / "logs" / "dual_leg_paper_session.json"
            sess.write_text(json.dumps({"session_id": "paper-cur"}), encoding="utf-8")
            payload = [
                {
                    "slug": "x",
                    "logged_at_hkt": "2026-11-07 09:00:00 HKT",
                    "session_id": "paper-old",
                    "trade_result": "SKIP",
                },
                {
                    "slug": "y",
                    "logged_at_hkt": "2026-11-07 10:00:00 HKT",
                    "session_id": "paper-cur",
                    "trade_result": "WIN",
                    "pnl": 1.0,
                },
            ]
            path.write_text(json.dumps(payload), encoding="utf-8")
            out = tail_dual_leg_v2_logs(mode="all", ledger="paper", limit=10, bot_root=root)
            self.assertEqual(out.get("session_id"), "paper-cur")
            self.assertEqual(len(out["lines"]), 1)
            self.assertEqual(out["lines"][0]["slug"], "y")
            trades = tail_dual_leg_v2_logs(mode="trades", ledger="paper", limit=10, bot_root=root)
            self.assertEqual(len(trades["lines"]), 1)
            self.assertEqual(trades["lines"][0]["slug"], "y")

    def test_paper_legacy_rows_without_session_still_shown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "logs" / "dual_leg_btc_paper_trades.json"
            path.parent.mkdir(parents=True)
            sess = root / "logs" / "dual_leg_paper_session.json"
            sess.write_text(json.dumps({"session_id": "paper-cur"}), encoding="utf-8")
            payload = [
                {"slug": "u", "logged_at_hkt": "2026-11-07 10:00:00 HKT", "pnl": 2.0},
            ]
            path.write_text(json.dumps(payload), encoding="utf-8")
            out = tail_dual_leg_v2_logs(mode="all", ledger="paper", limit=10, bot_root=root)
            self.assertEqual(len(out["lines"]), 1)
            self.assertEqual(out["lines"][0]["slug"], "u")


if __name__ == "__main__":
    unittest.main()
