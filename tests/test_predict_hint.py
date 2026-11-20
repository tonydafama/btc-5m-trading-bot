"""Glance-board hint math. No network."""

from __future__ import annotations

import unittest

from predict_hint import (
    bollinger_last,
    compose_payload,
    ema_last,
    judge,
    rsi_last,
    summarize_book,
    summarize_trades,
    supertrend_last,
    williams_r_last,
    window_bounds,
)


def _walk(n: int = 160, *, start: float, up: bool) -> list[float]:
    price = start
    out: list[float] = []
    step = 36.0 if up else -36.0
    pull = -22.0 if up else 22.0
    for i in range(n):
        if i == n - 1:
            price += 48.0 if up else -48.0
        elif i % 3 == 2:
            price += pull
        else:
            price += step
        out.append(price)
    return out


def _rising(n: int = 160) -> list[float]:
    return _walk(n, start=62_000.0, up=True)


def _falling(n: int = 160) -> list[float]:
    return _walk(n, start=62_000.0, up=False)


def _bundle(closes: list[float], *, bid: float, ask: float, buyer_taker: bool) -> tuple[dict, float]:
    start = 1_700_000_100 // 300 * 300
    rows = []
    prev = closes[0] - 0.4
    for i, close in enumerate(closes):
        open_ = prev
        high = max(open_, close) + 0.3
        low = min(open_, close) - 0.3
        opened = (start + i * 300) * 1000
        rows.append([opened, open_, high, low, close, 12, opened + 299_000, 0, 1, 4, 0, 0])
        prev = close
    last_open = rows[-1][0] // 1000
    now = last_open + 90
    last = closes[-1]
    depth = {
        "bids": [[f"{last - i * 0.5:.2f}", f"{bid}"] for i in range(20)],
        "asks": [[f"{last + 0.5 + i * 0.5:.2f}", f"{ask}"] for i in range(20)],
    }
    trades = [{"p": f"{last:.2f}", "q": "0.4", "m": not buyer_taker} for _ in range(40)]
    return {"klines": rows, "depth": depth, "trades": trades}, now


class WindowTests(unittest.TestCase):
    def test_windows_open_on_multiples_of_300(self) -> None:
        start, end, remaining = window_bounds(3010)
        self.assertEqual(start, 3000)
        self.assertEqual(end, 3300)
        self.assertAlmostEqual(remaining, 290.0)


class IndicatorTests(unittest.TestCase):
    def test_ema_of_flat_series_is_the_price(self) -> None:
        self.assertAlmostEqual(ema_last([5.0] * 12, 7) or 0, 5.0)

    def test_rsi_is_100_when_every_bar_rises(self) -> None:
        closes = [float(i) for i in range(1, 20)]
        self.assertEqual(rsi_last(closes, 6), 100.0)

    def test_williams_at_high_and_low(self) -> None:
        highs = [10, 11, 12, 13]
        lows = [8, 9, 10, 11]
        self.assertAlmostEqual(williams_r_last(highs, lows, [13, 13, 13, 13], 4) or 0, 0.0)
        self.assertAlmostEqual(williams_r_last(highs, lows, [8, 8, 8, 8], 4) or 0, -100.0)

    def test_bollinger_mid_on_flat_series(self) -> None:
        bb = bollinger_last([20.0] * 25, 20, 2)
        self.assertIsNotNone(bb)
        assert bb is not None
        self.assertAlmostEqual(bb["mid"], 20.0)
        self.assertAlmostEqual(bb["pct_b"], 0.5)

    def test_supertrend_follows_a_clean_trend(self) -> None:
        up_high, up_low, up_close = [], [], []
        down_high, down_low, down_close = [], [], []
        for i in range(40):
            up_close.append(100 + i * 2.0)
            up_high.append(up_close[-1] + 0.4)
            up_low.append(up_close[-1] - 0.4)
            down_close.append(200 - i * 2.0)
            down_high.append(down_close[-1] + 0.4)
            down_low.append(down_close[-1] - 0.4)
        up = supertrend_last(up_high, up_low, up_close, 10, 3)
        down = supertrend_last(down_high, down_low, down_close, 10, 3)
        self.assertEqual(up["trend"], "up")
        self.assertEqual(down["trend"], "down")


class JudgeTests(unittest.TestCase):
    def test_aligned_uptrend_continues_up(self) -> None:
        out = judge(
            {
                "move_sign": 1,
                "remaining_sec": 120,
                "ema_sign": 1,
                "macd_sign": 1,
                "supertrend_sign": 1,
                "rsi_sign": 1,
                "book_sign": 1,
                "trade_sign": 1,
                "stretch_against": False,
                "stretch_n": 0,
            }
        )
        self.assertEqual(out["direction"], "up")
        self.assertEqual(out["label"], "升")
        self.assertEqual(out["lean"], "continue")
        self.assertIn("延續", out["lean_text"])

    def test_stretched_up_move_leans_reversal(self) -> None:
        out = judge(
            {
                "move_sign": 1,
                "remaining_sec": 120,
                "ema_sign": -1,
                "macd_sign": -1,
                "supertrend_sign": -1,
                "rsi_sign": -1,
                "book_sign": -1,
                "trade_sign": -1,
                "stretch_against": True,
                "stretch_n": 2,
            }
        )
        self.assertEqual(out["lean"], "reverse")
        self.assertEqual(out["direction"], "down")
        self.assertEqual(out["label"], "跌")
        self.assertIn("反轉", out["lean_text"])


class PayloadTests(unittest.TestCase):
    def test_rising_market_hints_up_continuation(self) -> None:
        bundle, now = _bundle(_rising(), bid=3, ask=0.2, buyer_taker=True)
        payload = compose_payload(bundle, now=now)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["direction"], "up")
        self.assertEqual(payload["label"], "升")
        self.assertEqual(payload["lean"], "continue")
        names = [row["name"] for row in payload["indicators"]]
        self.assertIn("RSI 14", names)
        self.assertIn("RSI 6", names)
        self.assertIn("MACD 12, 26, 9", names)
        self.assertIn("EMA 7／25／99", names)
        self.assertGreater(payload["book"]["imbalance"], 0)
        self.assertNotIn("outcomePrices", str(payload))
        self.assertIn("chain.link", payload["settlement_url"])

    def test_falling_market_hints_down_continuation(self) -> None:
        bundle, now = _bundle(_falling(), bid=0.2, ask=3, buyer_taker=False)
        payload = compose_payload(bundle, now=now)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["direction"], "down")
        self.assertEqual(payload["label"], "跌")
        self.assertEqual(payload["lean"], "continue")
        self.assertLess(payload["book"]["imbalance"], 0)

    def test_book_and_trade_bias_are_labeled(self) -> None:
        book = summarize_book(
            {"bids": [["100", "5"]] * 5, "asks": [["101", "1"]] * 5}
        )
        self.assertEqual(book["bias"], "買盤較厚")
        trades = summarize_trades(
            [{"p": "100", "q": "2", "m": True}, {"p": "100", "q": "0.1", "m": False}],
            100,
        )
        self.assertEqual(trades["flow"], "主動賣較多")
        self.assertGreater(trades["sell_quote"], trades["buy_quote"])


if __name__ == "__main__":
    unittest.main()
