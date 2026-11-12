"""Mid-band Range80 paper rule, desk refusal, and a clean session reset."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

import midband_range80 as mb  # noqa: E402
from midband_range80_desk import apply_action, status_payload  # noqa: E402
from polymarket_fees import estimate_taker_fee  # noqa: E402


def book(ask: float, *, depth: float = 80.0, spread: float = 0.02) -> dict:
    return {"best_ask": ask, "best_bid": ask - spread, "spread": spread, "ask_depth_usd": depth}


class RuleTests(unittest.TestCase):
    def test_window_edges_are_inclusive(self) -> None:
        up, down = book(0.80), book(0.20)
        for t_rem in (40, 75, 40.0, 75.0):
            out = mb.decide_snapshot(t_remaining=t_rem, range_usd=80, up_book=up, down_book=down)
            self.assertEqual(out["action"], "enter", t_rem)
        for t_rem in (39.9, 75.1):
            out = mb.decide_snapshot(t_remaining=t_rem, range_usd=120, up_book=up, down_book=down)
            self.assertEqual(out["action"], "ignore", t_rem)

    def test_range_at_least_80(self) -> None:
        up, down = book(0.80), book(0.20)
        ok = mb.decide_snapshot(t_remaining=60, range_usd=80, up_book=up, down_book=down)
        self.assertEqual(ok["action"], "enter")
        low = mb.decide_snapshot(t_remaining=60, range_usd=79.99, up_book=up, down_book=down)
        self.assertEqual(low["action"], "skip_window")
        self.assertEqual(low["skip_category"], "range_below_80")

    def test_mid_band_edges_and_first_side_is_lower_ask(self) -> None:
        low = mb.decide_snapshot(
            t_remaining=50, range_usd=100, up_book=book(0.72), down_book=book(0.20)
        )
        high = mb.decide_snapshot(
            t_remaining=50, range_usd=100, up_book=book(0.20), down_book=book(0.88)
        )
        self.assertEqual((low["action"], low["side"]), ("enter", "up"))
        self.assertEqual((high["action"], high["side"]), ("enter", "down"))
        outside = mb.decide_snapshot(
            t_remaining=50, range_usd=100, up_book=book(0.71), down_book=book(0.89)
        )
        self.assertEqual(outside["action"], "wait")
        both = mb.decide_snapshot(
            t_remaining=50, range_usd=100, up_book=book(0.80), down_book=book(0.74)
        )
        self.assertEqual(both["side"], "down")
        self.assertEqual(both["ask"], 0.74)
        self.assertFalse(both["hedge"])
        self.assertEqual(both["legs"], 1)

    def test_skip_window_when_either_ask_above_094(self) -> None:
        skipped = mb.decide_snapshot(
            t_remaining=55, range_usd=200, up_book=book(0.80), down_book=book(0.9401)
        )
        self.assertEqual(skipped["action"], "skip_window")
        self.assertEqual(skipped["skip_category"], "ask_above_094")
        held = mb.decide_snapshot(
            t_remaining=55, range_usd=200, up_book=book(0.80), down_book=book(0.94)
        )
        self.assertEqual(held["action"], "enter")

    def test_coin_flip_waits_instead_of_buying(self) -> None:
        out = mb.decide_snapshot(
            t_remaining=70, range_usd=90, up_book=book(0.45), down_book=book(0.55)
        )
        self.assertEqual(out["action"], "wait")
        self.assertEqual(out["skip_category"], "coin_flip")

    def test_thin_or_wide_book_is_not_tradable(self) -> None:
        thin = mb.decide_snapshot(
            t_remaining=60,
            range_usd=100,
            up_book=book(0.80, depth=10),
            down_book=book(0.20),
        )
        wide = mb.decide_snapshot(
            t_remaining=60,
            range_usd=100,
            up_book=book(0.80, spread=0.07),
            down_book=book(0.20),
        )
        self.assertEqual(thin["action"], "wait")
        self.assertEqual(wide["action"], "wait")

    def test_previous_completed_candle_ignores_the_open_bar(self) -> None:
        now = 1_700_000_000_000
        older = [now - 700_000, 1, 80, 30, 40, 0, now - 400_000]
        done = [now - 400_000, 1, 200, 100, 150, 0, now - 100_000]
        still_open = [now - 60_000, 1, 500, 10, 20, 0, now + 240_000]
        self.assertEqual(mb.previous_completed_range_usd([older, done, still_open], now), 100.0)
        self.assertIsNone(mb.previous_completed_range_usd([still_open], now))

    def test_fee_matches_crypto_taker_formula(self) -> None:
        shares = 3.0 / 0.8
        self.assertEqual(estimate_taker_fee(shares, 0.8), round(shares * 0.07 * 0.8 * (1 - 0.8), 4))
        fill = mb.paper_fok_fill(book(0.80), unit_usd=3, min_shares=5)
        self.assertTrue(fill["filled"])
        self.assertGreaterEqual(fill["stake"], 5 * fill["fill_price"] - 0.02)
        self.assertEqual(fill["estimated_taker_fee"], estimate_taker_fee(fill["shares"], fill["fill_price"]))
        self.assertNotEqual(fill["execution_path"], "LIVE")
        self.assertTrue(fill["stake_floor_applied"])

    def test_slip_cannot_pay_above_the_band(self) -> None:
        fill = mb.paper_fok_fill(book(0.88), unit_usd=3, min_shares=1)
        self.assertFalse(fill["filled"])
        self.assertIn("slip_above_cap", fill["no_fill_reason"])


class DeskSafetyTests(unittest.TestCase):
    def test_missing_book_is_zero_and_unfunded(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="midband-desk-"))
        payload = status_payload(root)
        self.assertEqual(payload["mode"], "paper")
        self.assertFalse(payload["live_enabled"])
        self.assertFalse(payload["live_on_air"])
        self.assertEqual(payload["DRY_RUN"], "1")
        self.assertEqual(payload["REAL_MONEY"], "0")
        self.assertIsNone(payload["capital_usd"])
        self.assertEqual(payload["capital_label"], "待入金")
        self.assertEqual(payload["pnl"], 0)
        self.assertEqual(payload["trade_count"], 0)
        self.assertEqual(payload["deposit_address"], "0x8081D24BAc2456e5526F9C6eF80CC9e8269A259F")
        self.assertNotIn("0xB0B1", payload["deposit_address"])

    def test_to_live_refuses_and_writes_nothing(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="midband-live-"))
        (root / ".env").write_text("DRY_RUN=1\nREAL_MONEY=0\nTRADING_MODE=paper\n", encoding="utf-8")
        before = (root / ".env").read_text(encoding="utf-8")
        out = apply_action(root, "to_live")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "live_refused")
        self.assertFalse(out["live_enabled"])
        self.assertEqual((root / ".env").read_text(encoding="utf-8"), before)
        self.assertFalse((root / "config").exists())
        paper = apply_action(root, "to_paper")
        self.assertTrue(paper["ok"])
        self.assertEqual(paper["mode"], "paper")

    def test_session_filter_ignores_other_sessions(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="midband-sess-"))
        sons = root / "logs" / "sons"
        sons.mkdir(parents=True)
        (sons / "midband-range80_session.json").write_text(
            json.dumps({"session_id": "paper-midband-range80-test"}),
            encoding="utf-8",
        )
        (sons / "midband-range80_trades.json").write_text(
            json.dumps(
                [
                    {"session_id": "old", "entered": True, "pnl": 50, "trade_result": "WIN"},
                    {"session_id": "paper-midband-range80-test", "entered": False, "trade_result": "SKIP"},
                ]
            ),
            encoding="utf-8",
        )
        payload = status_payload(root)
        self.assertEqual(payload["trade_count"], 0)
        self.assertEqual(payload["pnl"], 0)

    def test_reset_only_touches_this_strategy(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="midband-reset-"))
        sons = root / "logs" / "sons"
        sons.mkdir(parents=True)
        kept = sons / "son-btc-2legs-v2-newnew-a_trades.json"
        kept.write_text("[{}]", encoding="utf-8")
        parent = root / "logs" / "dual_leg_btc_paper_trades.json"
        parent.write_text("[]", encoding="utf-8")
        script = ROOT / "scripts" / "reset_midband_range80_paper_session.py"
        old = sys.argv
        sys.argv = ["reset", str(root)]
        ns: dict = {"__name__": "reset_midband"}
        try:
            exec(script.read_text(encoding="utf-8"), ns)  # noqa: S102
            self.assertEqual(ns["main"](), 0)
        finally:
            sys.argv = old
        session = json.loads((sons / "midband-range80_session.json").read_text(encoding="utf-8"))
        trades = json.loads((sons / "midband-range80_trades.json").read_text(encoding="utf-8"))
        self.assertTrue(session["session_id"].startswith("paper-midband-range80-"))
        self.assertEqual(trades, [])
        self.assertEqual(session["capital_label"], "待入金")
        self.assertTrue(session["dry_run"])
        self.assertFalse(session["real_money"])
        self.assertEqual(kept.read_text(encoding="utf-8"), "[{}]")
        self.assertEqual(parent.read_text(encoding="utf-8"), "[]")

    def test_env_and_unit_stay_paper(self) -> None:
        env = (ROOT / "config" / "midband_range80_paper.env").read_text(encoding="utf-8")
        unit = (ROOT / "scripts" / "systemd-sons" / "btc-midband-range80-paper.service").read_text(encoding="utf-8")
        daemon = (ROOT / "scripts" / "midband_range80_paper_daemon.py").read_text(encoding="utf-8")
        mb.assert_paper_env(env)
        self.assertIn("DRY_RUN=1", env)
        self.assertIn("REAL_MONEY=0", env)
        self.assertIn("TRADING_MODE=paper", env)
        self.assertNotIn("EnvironmentFile=-/root/btc-5m-bot/.env", unit)
        self.assertIn("DRY_RUN=1", unit)
        self.assertIn("REAL_MONEY=0", unit)
        self.assertNotIn("execute_fok_intent", daemon)
        self.assertNotIn("REAL_MONEY=1", daemon)
        with self.assertRaises(RuntimeError):
            mb.assert_paper_env("DRY_RUN=0\nREAL_MONEY=1\nTRADING_MODE=live\n")

    def test_desk_card_is_paper_and_not_inside_the_live_lamp(self) -> None:
        html = (ROOT / "dashboard" / "templates" / "v1_only.html").read_text(encoding="utf-8")
        fold = html.split('id="midband-range80-fold"', 1)[1].split('id="paper-run-fold"', 1)[0]
        self.assertIn("先有波幅，先買中間價", fold)
        self.assertIn("待入金", fold)
        self.assertIn("真倉 OFF", fold)
        self.assertIn("LIVE OFF", fold)
        self.assertIn("0x8081D24BAc2456e5526F9C6eF80CC9e8269A259F", fold)
        self.assertNotIn("0xB0B1", fold)
        self.assertNotIn("data-sonu-on-air=\"live\"", fold)
        self.assertNotIn(">LIVE ON<", fold)
        logs = (ROOT / "dashboard" / "templates" / "family_logs.html").read_text(encoding="utf-8")
        self.assertIn('id="midband-range80"', logs)
        self.assertIn("function parseLine(", logs)


if __name__ == "__main__":
    unittest.main()
