# Bot health fixes — 2026-11-03 afternoon (HKT)

Droplet `143.110.240.132`. Son U live exec was left running.

## Done

- Desk header `#mode-chip` now follows Son U. If the gold row says 終極5m is live, the chip shows red **LIVE** even when `/api/son-u/live-control` is slow.
- Observer OOM: `MemoryHigh=768M` and `OOMScoreAdjust=400` so it is reclaimed before the host dies. Son U live exec `OOMScoreAdjust=-400`. Observer was not restarted (`NRestarts` stayed 2). It is active, about 190MB.
- V2 Son B stopped trade file deduped `3724 → 1850` rows. Backup: `son-btc-2legs-v2-b_trades.json.bak-dedupe-20261103`.
- Funding scans before 2026-11-01 archived to `logs/scans_archive_before_20261101.tar.gz` (13617 files). About 3224 recent scans left loose.
- `meme-paper-bot` radar restarted on `:8084` (`ENTRIES_DISABLED=1`, no new buys).

## Left as they are

- Parent V2 `max_opp` stays **0.72**. That is the loose control against newnew A at 0.55.
- `crypto-15m-observer` stays disabled. It was stopped on purpose on 2026-10-31 13:11 HKT, not crashed.
- V2 sons A/E trade files had no duplicate slugs. Their units stay inactive and registry stays STOPPED.
