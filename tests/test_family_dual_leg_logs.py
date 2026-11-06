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
            trades_only = tail_dual_leg_v2_logs(mode="trades", ledger="live", limit=10, bot_root=root)
            self.assertEqual(len(trades_only["lines"]), 1)
            self.assertEqual(trades_only["lines"][0]["slug"], "btc-updown-5m-a")

    def test_tail_paper_trades_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "logs" / "dual_leg_btc_paper_trades.json"
            path.parent.mkdir(parents=True)
            payload = [
                {
                    "slug": "x",
                    "logged_at_hkt": "2026-11-07 09:00:00 HKT",
                    "trade_result": "SKIP",
                },
                {
                    "slug": "y",
                    "logged_at_hkt": "2026-11-07 10:00:00 HKT",
                    "trade_result": "WIN",
                    "pnl": 1.0,
                },
            ]
            path.write_text(json.dumps(payload), encoding="utf-8")
            out = tail_dual_leg_v2_logs(mode="all", ledger="paper", limit=10, bot_root=root)
            self.assertEqual(out["lines"][0]["slug"], "y")
            trades = tail_dual_leg_v2_logs(mode="trades", ledger="paper", limit=10, bot_root=root)
            self.assertEqual(len(trades["lines"]), 1)
            self.assertEqual(trades["lines"][0]["slug"], "y")


if __name__ == "__main__":
    unittest.main()
