"""Independent desk controls: Live/Paper per bot, and the bet-size rule."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
sys.path.insert(0, str(DASHBOARD))

import desk_bots as db  # noqa: E402
import paper_board as pb  # noqa: E402


class DeskBotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="desk-bots-"))
        (self.root / "config").mkdir()
        (self.root / "config" / "sonu_live_exec.env").write_text(
            "DRY_RUN=1\nREAL_MONEY=0\nTRADING_MODE=paper\n",
            encoding="utf-8",
        )
        (self.root / ".env").write_text(
            "SONU_LIVE_ARMED_PREP=0\nDUAL_LEG_LIVE_DRY_RUN=1\nDUAL_LEG_REAL_MONEY=0\n",
            encoding="utf-8",
        )

    def test_bet_size_rule_5m_is_1x_and_v2_is_2x(self) -> None:
        sonu = db.set_bet_size(self.root, db.SONU_ID, 10)
        v2 = db.set_bet_size(self.root, db.V2_ID, 10)
        self.assertEqual(sonu["stake_usd"], "10")
        self.assertEqual(v2["stake_usd"], "20")
        self.assertIn("v2 5m btc：10 = $10", sonu["rule_line"])
        self.assertIn("v2：10 = $20", v2["rule_line"])
        env = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("SONU_STAKE_USD=10", env)
        self.assertIn("V2_STAKE_USD=20", env)
        self.assertIn("DUAL_LEG_UNIT_USD=10", env)
        self.assertIn("DUAL_LEG_LIVE_UNIT_USD=10", env)
        self.assertIn("DUAL_LEG_TOTAL_STAKE_USD=20", env)
        exec_env = (self.root / "config" / "v2_parent_live_exec.env").read_text(encoding="utf-8")
        self.assertIn("DUAL_LEG_LIVE_UNIT_USD=10", exec_env)
        self.assertNotIn("REAL_MONEY", exec_env)

    def test_bet_sizes_do_not_overwrite_each_other(self) -> None:
        db.set_bet_size(self.root, db.SONU_ID, 10)
        db.set_bet_size(self.root, db.V2_ID, 4)
        db.set_bet_size(self.root, db.SONU_ID, 7)
        self.assertEqual(db.stake_usd_for(db.SONU_ID, db.current_bet_size(self.root, db.SONU_ID)), 7)
        self.assertEqual(db.stake_usd_for(db.V2_ID, db.current_bet_size(self.root, db.V2_ID)), 8)

    def test_rejects_bad_bet_size(self) -> None:
        for raw in (0, -1, "abc", 500.01, None):
            out = db.handle_desk_action(self.root, db.V2_ID, "set_bet_size", bet_size=raw)
            self.assertFalse(out["ok"], raw)
            self.assertIn("bet size", out["error_tc"])

    def test_live_paper_buttons_are_independent(self) -> None:
        sonu = db.handle_desk_action(self.root, db.SONU_ID, "to_live", wallet_cash_usdc=100.52)
        self.assertTrue(sonu["ok"])
        self.assertEqual(sonu["desk_mode"], "prep")
        self.assertFalse(sonu.get("live_active"))
        self.assertEqual(db.v2_mode_view(self.root)["mode"], "paper")

        v2 = db.handle_desk_action(self.root, db.V2_ID, "to_live", wallet_cash_usdc=100.52)
        self.assertFalse(v2["live_active"])
        self.assertIn("V2_LIVE_ARMED_PREP=1", (self.root / ".env").read_text(encoding="utf-8"))
        env = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("SONU_LIVE_ARMED_PREP=1", env)
        self.assertIn("V2_LIVE_ARMED_PREP=1", env)
        self.assertIn("DUAL_LEG_LIVE_DRY_RUN=1", env)

        back = db.handle_desk_action(self.root, db.SONU_ID, "to_paper")
        self.assertTrue(back["ok"])
        self.assertEqual(back["desk_mode"], "paper")
        self.assertEqual(db.v2_mode_view(self.root)["mode"], "prep")

        v2_paper = db.handle_desk_action(self.root, db.V2_ID, "to_paper")
        self.assertEqual(v2_paper["mode"], "paper")
        self.assertIn("V2_LIVE_ARMED_PREP=0", (self.root / ".env").read_text(encoding="utf-8"))

    def test_v2_real_live_still_needs_wallet(self) -> None:
        env_path = self.root / ".env"
        env_path.write_text(
            env_path.read_text(encoding="utf-8") + "DUAL_LEG_LIVE_DRY_RUN=0\nDUAL_LEG_REAL_MONEY=1\n",
            encoding="utf-8",
        )
        blocked = db.handle_desk_action(self.root, db.V2_ID, "to_live", wallet_cash_usdc=40)
        self.assertFalse(blocked["ok"])
        self.assertEqual(db.v2_mode_view(self.root)["mode"], "paper")
        opened = db.handle_desk_action(self.root, db.V2_ID, "to_live", wallet_cash_usdc=120)
        self.assertFalse(opened.get("live_active"))

    def test_high_live_rows_use_bet_rule_and_keep_real_live_off(self) -> None:
        board = pb._high_live_board(
            self.root,
            {"DUAL_LEG_LIVE_DRY_RUN": "1", "DUAL_LEG_REAL_MONEY": "0"},
            None,
        )
        self.assertFalse(board["any_live"])
        rows = {row["id"]: row for row in board["rows"]}
        self.assertEqual(rows[db.SONU_ID]["stake_usd"], "10")
        self.assertEqual(rows[db.V2_ID]["stake_usd"], "20")
        self.assertEqual(rows[db.V2_ID]["mode"], "paper")
        db.handle_desk_action(self.root, db.V2_ID, "to_live", wallet_cash_usdc=200)
        board = pb._high_live_board(self.root, {"DUAL_LEG_LIVE_DRY_RUN": "1"}, None)
        v2 = {row["id"]: row for row in board["rows"]}[db.V2_ID]
        self.assertEqual(v2["mode"], "prep")
        self.assertFalse(board["any_live"])

    def test_template_has_independent_controls(self) -> None:
        html = (DASHBOARD / "templates" / "v1_only.html").read_text(encoding="utf-8")
        sonu = html.split('data-bot-id="son-btc-5m-ultimate-live"', 1)[1].split("</article>", 1)[0]
        v2 = html.split('data-bot-id="dual-leg-live-v2"', 1)[1].split("</article>", 1)[0]
        for block, live_id in ((sonu, "c15m-btn-live"), (v2, "v2-btn-live")):
            self.assertIn('data-bot-action="to_live"', block)
            self.assertIn('data-bot-action="to_paper"', block)
            self.assertIn("bet-size-input", block)
            self.assertIn(f'id="{live_id}"', block)
        self.assertIn('data-multiplier="1"', sonu)
        self.assertIn('data-multiplier="2"', v2)
        self.assertIn("/api/desk/bots/", html)
        self.assertIn("deskRuleLine", html)
        self.assertIn("bindDeskControls", html)
        self.assertNotIn("el.innerHTML = extra.map", html)

    def test_v2_live_on_air_off_during_entry_pause(self) -> None:
        from unittest.mock import patch

        with patch.object(db, "v2_unit_is_live", return_value=True):
            with patch("dual_leg_live.entry_pause_view", return_value={"active": True}):
                view = db.v2_mode_view(self.root)
        self.assertTrue(view["live_active"])
        self.assertFalse(view["live_on_air"])


if __name__ == "__main__":
    unittest.main()
