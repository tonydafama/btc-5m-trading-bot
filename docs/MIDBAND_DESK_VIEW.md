# Mid-band Range80 · Desk view toggle

Finance chat starts and stops **real money** on the droplet. The Family Desk
Paper/Live buttons on the midband card only switch **`desk_view`** (which
ledger the card shows). They write `logs/sons/midband-range80_desk_view.json`
and never call `systemctl`, never change `DRY_RUN` / `REAL_MONEY`, and never
touch Parent V2 or newnew-A.

When real money is on air and the user selects **Paper**, stats come from the
latest `logs/sons/archive/midband-range80_*-before-live-*.json` paper snapshot.

API: `GET|POST /api/midband-range80` with body `{"action":"to_paper"|"to_live"}`.
