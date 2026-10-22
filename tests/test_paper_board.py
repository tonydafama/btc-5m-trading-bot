"""Display-only board: bots, paper ledgers, and research stay in separate sections."""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"
sys.path.insert(0, str(DASHBOARD))

import paper_board  # noqa: E402


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


class PaperBoardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self._tmp_root())
        self.bot = self.tmp / "btc"
        self.funding = self.tmp / "funding-carry-bot"
        self.bot.mkdir()
        self.funding.mkdir()

    def _tmp_root(self) -> str:
        import tempfile

        self.addCleanup(lambda: None)
        root = tempfile.mkdtemp(prefix="paper-board-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        return root

    def _build(self, **kwargs):
        kwargs.setdefault("bot_root_path", self.bot)
        kwargs.setdefault("funding_root_path", self.funding)
        kwargs.setdefault("env", {"TRADING_MODE": "paper", "ETH_TRADING_MODE": "paper"})
        kwargs.setdefault("service_states", {unit: "inactive" for unit in paper_board.BOT_UNITS})
        kwargs.setdefault("processes", [])
        kwargs.setdefault("loaders", {"crypto15m": None, "dual_leg": None})
        kwargs.setdefault("dashboard_self", False)
        return paper_board.build_all_paper_strategies(**kwargs)

    def test_sections_and_paper_pnl(self) -> None:
        _write(
            self.bot / "logs" / "trade_log_paper.json",
            [
                {"entered": False, "trade_result": "SKIPPED", "timestamp_hkt": "2026-10-01 09:00:00"},
                {
                    "entered": True,
                    "trade_result": "WIN",
                    "pnl": 1.5,
                    "timestamp_hkt": "2026-10-02 10:00:00",
                },
                {
                    "entered": True,
                    "trade_result": "LOSS",
                    "pnl": -0.25,
                    "timestamp_hkt": "2026-10-03 11:15:00",
                },
            ],
        )
        _write(
            self.bot / "logs" / "trade_log_eth_paper.json",
            [{"entered": True, "trade_result": "WIN", "pnl": 2, "timestamp_hkt": "2026-10-04 12:00:00"}],
        )
        _write(
            self.bot / "logs" / "backtests" / "fair_value_oos.json",
            {
                "in_sample": {"n": 10, "wr": 0.9, "net": 100},
                "oos": {"n": 40, "win_rate": 0.55, "net_pnl": -2.5},
            },
        )
        now = datetime.now(timezone.utc).replace(microsecond=0)
        _write(
            self.funding / "logs" / "scans" / "scan_20261022_070000.json",
            {
                "ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "venues": {"binance": {"BTC": {"funding_rate": 0.0001}}, "kucoin": {"ETH": {"funding_rate": -0.0002}}},
                "ideas": [
                    {"flag": False, "net_apr": 0.012},
                    {"flag": False, "net_apr": -0.04},
                ],
            },
        )
        signal = {
            "placed_real_order": False,
            "idea": {"flag": False, "net_apr": 0.012},
            "hypothetical": {"notional_usd": 1000},
        }
        _write(self.funding / "logs" / "paper" / "signals.jsonl", json.dumps(signal) + "\n")

        board = self._build(
            service_states={
                "btc-5m-observer": "active",
                "eth-5m-observer": "inactive",
                "crypto-15m-observer": "inactive",
                "btc-5m-dashboard": "inactive",
            },
            processes=[
                {"pid": "42", "cmd": "/usr/bin/python main.py --loop", "cwd": str(self.funding)},
            ],
            loaders={
                "crypto15m": {
                    "mode": "paper",
                    "combined_overall_pnl": 3.25,
                    "btc": {"trades_entered": 2, "overall_pnl": 1},
                    "eth": {"trades_entered": 1, "overall_pnl": 2.25},
                    "session": {"updated_at_hkt": "2026-10-05 08:00:00"},
                },
                "dual_leg": None,
            },
        )

        self.assertEqual([row["id"] for row in board["bots"]], [
            "btc-5m-observer",
            "eth-5m-observer",
            "crypto-15m-observer",
            "btc-5m-dashboard",
            "funding-carry-loop",
        ])
        by_id = {row["id"]: row for row in board["bots"]}
        self.assertEqual(by_id["btc-5m-observer"]["status_label"], "RUNNING")
        self.assertEqual(by_id["btc-5m-observer"]["mode"], "PAPER")
        self.assertEqual(by_id["eth-5m-observer"]["status_label"], "STOPPED")
        self.assertEqual(by_id["funding-carry-loop"]["status_label"], "RUNNING")
        self.assertEqual(by_id["funding-carry-loop"]["mode"], "PAPER")
        self.assertNotIn("FLAG", by_id["funding-carry-loop"]["description"])
        self.assertNotIn("APR", by_id["funding-carry-loop"]["description"])

        names = [row["name"] for row in board["paper"]]
        self.assertEqual(names, ["BTC 5m Package2+", "ETH 5m Package2+", "BTC+ETH 15m"])
        btc = board["paper"][0]
        self.assertEqual(btc["mode"], "PAPER")
        self.assertEqual(btc["trade_count"], 2)
        self.assertEqual(btc["pnl"], 1.25)
        self.assertEqual(btc["last_activity"], "2026-10-03 11:15 HKT")
        self.assertIn("唔係真錢", btc["note"])
        self.assertFalse(btc["real_money"])
        eth = board["paper"][1]
        self.assertEqual(eth["pnl"], 2)
        c15 = board["paper"][2]
        self.assertEqual(c15["trade_count"], 3)
        self.assertEqual(c15["pnl"], 3.25)
        self.assertEqual(c15["last_activity"], "2026-10-05 08:00 HKT")

        self.assertEqual(board["hero"]["title"], "全部 Paper · 冇 live 真下單")
        self.assertEqual(board["hero"]["bots_running"], 2)
        self.assertEqual(board["hero"]["paper_count"], 3)
        self.assertEqual(board["hero"]["experiment_count"], 2)
        self.assertFalse(board["hero"]["any_live"])

        fair = board["experiments"][0]
        self.assertIn("OOS n=40", fair["summary"])
        self.assertIn("勝率 55%", fair["summary"])
        self.assertIn("net -$2.50", fair["summary"])
        self.assertNotIn("100", fair["summary"])
        self.assertFalse(fair["is_bot"])
        funding = board["experiments"][1]
        self.assertIn("未有 FLAG", funding["summary"])
        self.assertIn("+$0.00", funding["summary"])
        self.assertIn("binance BTC", funding["snapshot"])
        self.assertIn("最高 net APR +1.20%", funding["detail"])

    def test_live_mode_does_not_pretend_paper_is_flat(self) -> None:
        _write(
            self.bot / "logs" / "trade_log_paper.json",
            [{"entered": True, "trade_result": "WIN", "pnl": 4, "timestamp_hkt": "2026-10-01 01:00:00"}],
        )
        board = self._build(env={"TRADING_MODE": "live", "ETH_TRADING_MODE": "paper", "CRYPTO_15M_TRADING_MODE": "live"})
        self.assertTrue(board["hero"]["any_live"])
        self.assertIn("LIVE", board["hero"]["title"])
        self.assertEqual(board["bots"][0]["mode"], "LIVE")
        self.assertEqual(board["bots"][2]["mode"], "LIVE")
        self.assertIn("而家 LIVE", board["paper"][0]["note"])
        self.assertEqual(board["paper"][0]["pnl"], 4)
        self.assertEqual(board["paper"][0]["mode"], "PAPER")

    def test_dual_leg_follows_env_and_summary(self) -> None:
        hidden = self._build()
        self.assertNotIn("dual-leg-paper", [row["id"] for row in hidden["paper"]])

        shown = self._build(env={"DUAL_LEG_ENABLED": "1", "TRADING_MODE": "paper"})
        row = next(item for item in shown["paper"] if item["id"] == "dual-leg-paper")
        self.assertEqual(row["mode"], "PAPER")
        self.assertIn("唔係真錢", row["note"])

        from_summary = self._build(loaders={"crypto15m": None, "dual_leg": {"pnl": -1.5, "closed": 4}})
        row = next(item for item in from_summary["paper"] if item["id"] == "dual-leg-paper")
        self.assertEqual(row["pnl"], -1.5)
        self.assertEqual(row["trade_count"], 4)

        present_but_down = self._build(loaders={"crypto15m": None, "dual_leg": {"_unavailable": True}})
        self.assertIn("dual-leg-paper", [row["id"] for row in present_but_down["paper"]])

        forced_off = self._build(
            env={"DUAL_LEG_ENABLED": "0"},
            loaders={"crypto15m": None, "dual_leg": {"pnl": 9, "closed": 3}},
        )
        self.assertNotIn("dual-leg-paper", [row["id"] for row in forced_off["paper"]])

    def test_fair_value_empty_copy_and_15m_live_split(self) -> None:
        empty = self._build()
        self.assertEqual(empty["experiments"][0]["summary"], "腳本有，未當 bot 跑")
        self.assertIn("未有 trade_log_paper.json", empty["paper"][0]["note"])

        live = self._build(
            loaders={
                "crypto15m": {"mode": "live", "combined_overall_pnl": 99, "btc": {"trades_entered": 8}, "eth": {"trades_entered": 1}},
                "dual_leg": None,
            }
        )
        c15 = live["paper"][2]
        self.assertIsNone(c15["pnl"])
        self.assertIn("LIVE", c15["note"])
        self.assertNotEqual(c15["pnl_label"], "+$99.00")

    def test_funding_pidfile_and_ignores_unrelated_loop(self) -> None:
        unrelated = paper_board.funding_loop_status(
            self.funding,
            processes=[{"pid": "9", "cmd": "python /tmp/other/main.py --loop", "cwd": "/tmp/other"}],
        )
        self.assertFalse(unrelated["running"])

        pid_path = self.funding / "logs" / "funding-carry.pid"
        _write(pid_path, str(os.getpid()))
        via_pid = paper_board.funding_loop_status(self.funding, processes=[])
        self.assertTrue(via_pid["running"])
        self.assertEqual(via_pid["via"], "pidfile")

        service = paper_board.funding_carry_service_row(
            self.funding,
            processes=[{"pid": "3", "cmd": "python /root/funding-carry-bot/main.py --loop", "cwd": "/"}],
        )
        self.assertTrue(service["active"])
        self.assertEqual(service["label"], "Funding PAPER")
        self.assertEqual(service["state"], "active")

    def test_dashboard_self_counts_as_running(self) -> None:
        board = self._build(dashboard_self=True)
        dash = next(row for row in board["bots"] if row["id"] == "btc-5m-dashboard")
        self.assertEqual(dash["status_label"], "RUNNING")
        self.assertEqual(dash["mode"], "—")
        self.assertEqual(board["hero"]["bots_running"], 1)

    def test_template_has_three_sections(self) -> None:
        html = (DASHBOARD / "templates" / "v1_only.html").read_text(encoding="utf-8")
        self.assertIn("而家跑緊嘅 Bot", html)
        self.assertIn("Paper 交易", html)
        self.assertIn("實驗 / 研究", html)
        self.assertIn("唔係下單 bot；只係研究或 scanner。", html)
        self.assertIn("/api/all-paper-strategies", html)
        self.assertIn("全部 Paper · 載入中", html)
        self.assertLess(html.index("all-paper-board"), html.index('class="hero"'))


if __name__ == "__main__":
    unittest.main()
