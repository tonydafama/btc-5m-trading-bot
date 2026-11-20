"""The new tab is display-only and linked from Desk and Logs."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "dashboard" / "templates" / "predict.html"
APP = ROOT / "dashboard" / "app.py"
DESK = ROOT / "dashboard" / "templates" / "v1_only.html"
LOGS = ROOT / "dashboard" / "templates" / "family_logs.html"


class PredictPageTests(unittest.TestCase):
    def test_page_is_a_hint_not_a_trading_control(self) -> None:
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn("升跌提示", html)
        self.assertIn(">Desk</a>", html)
        self.assertIn('href="/logs"', html)
        self.assertIn("延續定反轉", html)
        self.assertIn("只係提示", html)
        self.assertIn("Chainlink BTC/USD TWAP", html)
        self.assertIn("https://data.chain.link/streams/btc-usd-twap-60s-streams", html)
        self.assertIn("RSI 6", html)
        self.assertIn("MACD", html)
        self.assertIn("掛單深度同偏向", html)
        self.assertIn("5 股", html)
        self.assertIn("$4.25", html)
        self.assertIn("$1", html)
        self.assertIn("FOK", html)
        self.assertIn("DRY_RUN 開", html)
        self.assertIn("呢頁唔會落單", html)
        self.assertIn("唔用 Polymarket 賠率", html)
        self.assertNotIn("to_live", html)
        self.assertNotIn("data-bot-action", html)
        self.assertNotIn("data-midband-action", html)
        self.assertNotIn("LIVE ON", html)
        self.assertNotIn("0x", html)
        self.assertNotIn("/api/desk/bots", html)
        self.assertNotIn("/api/midband-range80", html)
        self.assertIn("/api/predict-hint", html)
        self.assertNotIn("method=\"post\"", html.lower())

    def test_routes_stay_read_only(self) -> None:
        app = APP.read_text(encoding="utf-8")
        self.assertIn('@app.route("/predict")', app)
        self.assertIn('@app.route("/api/predict-hint")', app)
        self.assertIn("public_prep_status", app)
        self.assertNotIn("maybe_place_fok", app)
        self.assertNotIn("run_prep", app)
        self.assertNotIn("place_buy_order", app)
        self.assertIn('def index():\n    return render_template("v1_only.html")', app)
        self.assertIn('family_logs.html', app)

    def test_existing_nav_gains_a_link_without_removing_logs(self) -> None:
        desk = DESK.read_text(encoding="utf-8")
        logs = LOGS.read_text(encoding="utf-8")
        self.assertIn('href="/predict"', desk)
        self.assertIn('href="/logs"', desk)
        self.assertIn("c15m-on-air-lamp", desk)
        self.assertIn('href="/predict"', logs)
        self.assertIn('href="/"', logs)
        self.assertIn("function parseLine(", logs)
        self.assertIn("Do NOT delete parseLine / statusTag / renderRows", logs)


if __name__ == "__main__":
    unittest.main()
