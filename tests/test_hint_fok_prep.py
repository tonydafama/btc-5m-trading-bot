"""Default-off FOK prep. These tests must not touch the network."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import hint_fok_prep
from hint_fok_prep import gates_open, maybe_place_fok, plan_fok, public_prep_status, run_prep, size_fok

ROOT = Path(__file__).resolve().parents[1]


class SizingTests(unittest.TestCase):
    def test_common_asks_floor_one_dollar_to_five_shares(self) -> None:
        at_80 = size_fok(0.80)
        at_85 = size_fok(0.85)
        self.assertEqual(at_80["shares"], 5.0)
        self.assertEqual(at_80["notional_usd"], 4.0)
        self.assertTrue(at_80["floored_to_min_shares"])
        self.assertEqual(at_85["shares"], 5.0)
        self.assertEqual(at_85["notional_usd"], 4.25)
        self.assertEqual(at_85["intended_usd"], 1.0)

    def test_cheap_ask_can_spend_the_one_dollar(self) -> None:
        sized = size_fok(0.10)
        self.assertGreaterEqual(sized["shares"], 5.0)
        self.assertAlmostEqual(sized["notional_usd"], 1.0)
        self.assertFalse(sized["floored_to_min_shares"])


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            key: os.environ.get(key)
            for key in (
                "HINT_FOK_ENABLED",
                "HINT_FOK_DRY_RUN",
                "HINT_FOK_ARMED",
                "HINT_FOK_REAL_MONEY",
                "DRY_RUN",
                "REAL_MONEY",
                "LIVE_ARMED",
            )
        }
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults_are_shut_even_if_global_live_flags_are_on(self) -> None:
        os.environ["DRY_RUN"] = "0"
        os.environ["REAL_MONEY"] = "1"
        os.environ["LIVE_ARMED"] = "1"
        self.assertFalse(gates_open())
        status = public_prep_status()
        self.assertTrue(status["dry_run"])
        self.assertFalse(status["armed"])
        self.assertFalse(status["real_money"])
        self.assertFalse(status["enabled"])
        self.assertFalse(status["will_send"])
        self.assertFalse(status["page_can_send"])
        self.assertIsNone(status["systemd_unit"])
        self.assertEqual(status["separate_from"], "midband-range80")

    def test_one_open_gate_is_not_enough(self) -> None:
        os.environ["HINT_FOK_ENABLED"] = "1"
        os.environ["HINT_FOK_DRY_RUN"] = "0"
        os.environ["HINT_FOK_ARMED"] = "1"
        self.assertFalse(gates_open())

    def test_truthy_words_do_not_arm(self) -> None:
        os.environ["HINT_FOK_ENABLED"] = "true"
        os.environ["HINT_FOK_DRY_RUN"] = "false"
        os.environ["HINT_FOK_ARMED"] = "yes"
        os.environ["HINT_FOK_REAL_MONEY"] = "on"
        self.assertFalse(gates_open())

    def _open_gates(self) -> None:
        os.environ["HINT_FOK_ENABLED"] = "1"
        os.environ["HINT_FOK_DRY_RUN"] = "0"
        os.environ["HINT_FOK_ARMED"] = "1"
        os.environ["HINT_FOK_REAL_MONEY"] = "1"

    def test_closed_gates_never_call_the_sender(self) -> None:
        calls = []

        def place(plan):
            calls.append(plan)
            return {"ok": True}

        plan = plan_fok(direction="up", ask=0.80, token_id="token-up", window_start=3000, slug="btc-updown-5m-3000")
        result = maybe_place_fok(plan, place_fn=place, ledger_path=Path("/tmp/should-not-write.json"))
        self.assertTrue(result["blocked"])
        self.assertFalse(result["placed"])
        self.assertEqual(calls, [])
        self.assertFalse(Path("/tmp/should-not-write.json").exists())

    def test_open_gates_send_one_fok_per_window(self) -> None:
        self._open_gates()
        calls = []

        def place(plan):
            calls.append(plan)
            return {"order_type": "FOK"}

        ledger = Path("/tmp/hint-fok-test-ledger.json")
        lock = ledger.with_suffix(".lock")
        ledger.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
        plan = plan_fok(direction="down", ask=0.85, token_id="token-down", window_start=3300, slug="btc-updown-5m-3300")
        first = maybe_place_fok(plan, place_fn=place, ledger_path=ledger)
        second = maybe_place_fok(plan, place_fn=place, ledger_path=ledger)
        self.assertTrue(first["placed"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["notional_usd"], 4.25)
        self.assertEqual(calls[0]["shares"], 5.0)
        self.assertEqual(calls[0]["order_type"], "FOK")
        self.assertFalse(second["placed"])
        self.assertIn("已經試過", second["message"])
        self.assertEqual(len(calls), 1)
        ledger.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)

    def test_send_errors_do_not_keep_wallet_text(self) -> None:
        self._open_gates()

        def place(_plan):
            raise RuntimeError("funder 0xabc123456789abcd rejected")

        ledger = Path("/tmp/hint-fok-test-ledger-redact.json")
        ledger.unlink(missing_ok=True)
        plan = plan_fok(direction="up", ask=0.80, token_id="token-up", window_start=3600, slug="btc-updown-5m-3600")
        result = maybe_place_fok(plan, place_fn=place, ledger_path=ledger)
        self.assertNotIn("0xabc", result["message"])
        self.assertIn("[redacted]", result["message"])
        ledger.unlink(missing_ok=True)
        ledger.with_suffix(".lock").unlink(missing_ok=True)

    def test_run_prep_does_not_quote_or_send_while_shut(self) -> None:
        def boom(_hint=None):
            raise AssertionError("should not run")

        result = run_prep(hint_fn=boom, quote_fn=boom, place_fn=boom)
        self.assertTrue(result["blocked"])
        self.assertFalse(result["placed"])
        self.assertIn("唔會落單", result["message"])


class IsolationTests(unittest.TestCase):
    def test_module_does_not_import_midband_or_enable_a_unit(self) -> None:
        text = (ROOT / "hint_fok_prep.py").read_text(encoding="utf-8")
        script = (ROOT / "scripts" / "hint_fok_prep.py").read_text(encoding="utf-8")
        self.assertNotIn("import midband", text)
        self.assertNotIn("btc-midband-range80-live", text)
        self.assertNotIn("systemctl enable", script)
        self.assertNotIn("load_dotenv", script)
        self.assertEqual(list(ROOT.glob("scripts/*hint*fok*.service")), [])
        self.assertNotIn('os.environ["DRY_RUN"]', text)
        self.assertNotIn('os.environ["REAL_MONEY"]', text)
        self.assertIsNone(hint_fok_prep.public_prep_status()["systemd_unit"])


if __name__ == "__main__":
    unittest.main()
