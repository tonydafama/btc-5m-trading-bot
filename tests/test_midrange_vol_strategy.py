from __future__ import annotations

import unittest

from midrange_vol_strategy import evaluate_entry, note_first_to_band, skip_reason


class MidrangeVolRulesTest(unittest.TestCase):
    def test_skip_coinflip(self) -> None:
        self.assertEqual(skip_reason(0.50, 0.50), "both_coinflip_zone")

    def test_first_to_band_and_enter(self) -> None:
        track: dict = {}
        note_first_to_band(track, up_ask=0.76, down_ask=0.30, t_remaining=70)
        dec, reason = evaluate_entry(
            t_remaining=55,
            prior_range_usd=120.0,
            up_ask=0.76,
            down_ask=0.30,
            track=track,
        )
        self.assertEqual(reason, "enter")
        self.assertIsNotNone(dec)
        assert dec is not None
        self.assertEqual(dec["side"], "up")

    def test_prior_range_blocks(self) -> None:
        track = {"first_side": "up"}
        dec, reason = evaluate_entry(
            t_remaining=50,
            prior_range_usd=50,
            up_ask=0.80,
            down_ask=0.25,
            track=track,
        )
        self.assertIsNone(dec)
        self.assertEqual(reason, "prior_5m_range_below_80")


if __name__ == "__main__":
    unittest.main()
