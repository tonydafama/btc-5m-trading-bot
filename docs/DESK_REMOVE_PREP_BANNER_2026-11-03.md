# Family Desk — remove Live box prep banner (2026-11-03 HKT)

## Removed

- `#hl-sub` — API subtitle (e.g. 預備區 · 紙上 rehearsal · 真倉 OFF…)
- `#hl-badge` / `#hl-run` — PREP · ARMED / PREP · PAPER / READY chips in gold Live header
- `.hl-split-note` — 「兩個 bot 各自 Live / Paper…」 help blurb
- Card chips **PREP · ARMED** / **PAPER PREP** (kept **LIVE** / **曾live** only)

## Kept

- **Live · Son U** section title
- Per-bot Live/Paper toggles + bet size on cards
- `#hl-note` wallet/footer line
- Top-right **Logs** chip

## Verify

```bash
curl -s http://127.0.0.1:8082/ | grep -E '預備區|紙上 rehearsal|PREP · ARMED|兩個 bot 各自 Live' && echo FAIL || echo OK
```
