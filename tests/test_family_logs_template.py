"""family_logs.html must keep core log helpers (Son U lock regression guard)."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAMILY = ROOT / "dashboard" / "templates" / "family_logs.html"
SEAT_JS = ROOT / "dashboard" / "static" / "sonu-seat-display.js"


class FamilyLogsTemplateTests(unittest.TestCase):
    def test_core_helpers_present(self) -> None:
        html = FAMILY.read_text(encoding="utf-8")
        self.assertIn("function parseLine(", html)
        self.assertIn("function statusTag(", html)
        self.assertIn("function renderRows(", html)
        self.assertIn("Do NOT delete parseLine / statusTag / renderRows", html)

    def test_revamped_sonu_logs_ui(self) -> None:
        html = FAMILY.read_text(encoding="utf-8")
        self.assertIn("Log · Son U", html)
        self.assertIn("終極5m · Live", html)
        self.assertIn("sonu-paper-fold", html)
        self.assertIn("sonu_only=1", html)
        self.assertIn("isSonuTradeRow", html)
        self.assertIn("暫無成交", html)
        self.assertIn("isFillRow", html)
        self.assertIn("/api/family-bots", html)
        self.assertIn("data-live-mode=\"all\"", html)
        self.assertIn("let liveMode = 'all'", html)
        self.assertIn("/api/bot-log-tail?id=", html)
        self.assertIn("function findSonULiveBot(", html)
        self.assertIn("function statusLineFromBot(", html)
        self.assertIn("function normalizeTailStats(", html)
        self.assertIn("son-btc-5m-ultimate-live", html)
        self.assertNotIn("LOCKED · Live", html)
        self.assertNotIn("id=son-btc", html)
        self.assertIn("/api/son-u/live-control", html)
        self.assertIn("pnl=", html)

    def test_fill_lines_are_trades_and_seat_ignores_fill_count(self) -> None:
        html = FAMILY.read_text(encoding="utf-8")
        script = html.split("<script>", 1)[1].split("document.querySelectorAll", 1)[0]
        probe = r"""
const lines = [
  '2026-11-03 05:00:03 · SKIP · pnl=— $0.00 · btc-updown-5m-1793652900',
  '2026-11-02 22:50:41 · UP · up · pnl=+1.40 $10.00 ask=0.87 · btc-updown-5m-1793630700',
  '2026-11-03 01:58:11 · UP · down · pnl=-10.13 $10.00 ask=0.82 · btc-updown-5m-1793641800',
];
const fills = lines.filter(isFillRow);
const skips = lines.filter(isSkipRow);
const paperBot = { id: 'son-btc-5m-ultimate-live', status: 'RUNNING · paper · go-live READY', trade_count: 8 };
const liveBot = { mode: 'live', live_active: true, running: true, trade_count: 0 };
const out = {
  fillN: fills.length,
  skipN: skips.length,
  win: statusTag(lines[1]).label,
  loss: statusTag(lines[2]).label,
  paper: statusLineFromBot(paperBot),
  live: statusLineFromBot(liveBot),
};
console.log(JSON.stringify(out));
"""
        proc = subprocess.run(
            ["node", "--input-type=commonjs", "-e", "global.window = global;\n" + script + probe],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(got["fillN"], 2)
        self.assertEqual(got["skipN"], 1)
        self.assertEqual(got["win"], "FILL")
        self.assertEqual(got["loss"], "LOSS")
        self.assertEqual(got["paper"], "紙上預備 · 跑緊")
        self.assertEqual(got["live"], "真倉 ON · 跑緊")

    def test_desk_any_live_follows_dry_run_not_trade_count(self) -> None:
        probe = """
globalThis.window = globalThis;
""" + SEAT_JS.read_text(encoding="utf-8") + """
const paperOnly = { high_live_board: { any_live: true, rows: [{
  dry_run: true, live_enabled: false, trade_count: 8,
  status_label: '跑緊 · 真倉',
  change: 'Live · 切換→Paper（暫停真倉）· 真倉 OFF',
}] } };
const liveBoard = { high_live_board: { any_live: false, rows: [{
  dry_run: false, live_enabled: true, trade_count: 0,
  status_label: '跑緊 · 真倉',
  change: '真倉 ON',
}] } };
const patched = sonuSeatDisplay.patchBoard(paperOnly);
const live = sonuSeatDisplay.patchBoard(liveBoard);
const row = patched.high_live_board.rows[0];
console.log(JSON.stringify({
  any: patched.high_live_board.any_live,
  label: row.status_label,
  change: row.change,
  liveAny: live.high_live_board.any_live,
  liveLabel: live.high_live_board.rows[0].status_label,
}));
"""
        proc = subprocess.run(
            ["node", "--input-type=commonjs", "-e", probe],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertFalse(got["any"])
        self.assertEqual(got["label"], "跑緊 · 紙上預備")
        self.assertEqual(got["change"], "紙上預備 · 真倉未開")
        self.assertTrue(got["liveAny"])
        self.assertEqual(got["liveLabel"], "跑緊 · 真倉")


if __name__ == "__main__":
    unittest.main()
