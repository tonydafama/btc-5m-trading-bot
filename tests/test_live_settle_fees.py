"""Live dual-leg settlement must net Polymarket taker fees."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dual_leg_live as live  # noqa: E402
from polymarket_fees import CRYPTO_TAKER_FEE_RATE, estimate_taker_fee  # noqa: E402


class LiveSettleFeeTests(unittest.TestCase):
    def test_leg_from_fill_shape_includes_fee(self) -> None:
        shares = 25.0
        price = 0.4
        fee = estimate_taker_fee(shares, price)
        self.assertGreater(fee, 0)
        leg = {
            "side": "up",
            "ask": price,
            "stake": 10.0,
            "shares": shares,
            "estimated_taker_fee": round(fee, 4),
        }
        self.assertAlmostEqual(live.leg_taker_fee_usd(leg), fee, places=4)

    def test_hedged_win_pnl_is_net_of_fees(self) -> None:
        first = {"side": "up", "ask": 0.4, "stake": 10.0, "shares": 25.0}
        second = {"side": "down", "ask": 0.35, "stake": 10.0, "shares": 28.571429}
        row = live.account_live_trade_pnl(
            {"first_leg": first, "second_leg": second},
            "up",
        )
        gross = 25.0 - 20.0
        fee = float(row["total_estimated_fee"])
        self.assertGreater(fee, 0)
        self.assertAlmostEqual(float(row["gross_pnl"]), gross, places=2)
        self.assertAlmostEqual(float(row["pnl"]), round(gross - fee, 2), places=2)

    def test_refresh_lowers_old_zero_fee_settlement(self) -> None:
        row = {
            "status": "closed",
            "outcome": "UP",
            "settled_at_hkt": "2026-11-03 18:00:00 HKT",
            "pnl": 2.22,
            "total_estimated_fee": 0.0,
            "first_leg": {"side": "up", "ask": 0.34, "stake": 10.0, "shares": 29.411765},
            "second_leg": {"side": "down", "ask": 0.45, "stake": 10.0, "shares": 22.222222},
        }
        refreshed = live.account_live_trade_pnl(dict(row), "up")
        fee = float(refreshed["total_estimated_fee"])
        self.assertGreater(fee, 0)
        self.assertAlmostEqual(
            float(refreshed["pnl"]),
            float(refreshed["gross_pnl"]) - fee,
            places=2,
        )

    def test_fee_rate_matches_polymarket_helper(self) -> None:
        self.assertEqual(CRYPTO_TAKER_FEE_RATE, 0.07)


if __name__ == "__main__":
    unittest.main()
