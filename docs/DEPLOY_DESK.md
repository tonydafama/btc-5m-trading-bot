# Desk dashboard (:8082) deploy notes

Production serves the V1 desk at **port 8082** (proxy) → Flask **8083** (`dashboard/app.py`). The home page is `dashboard/templates/v1_only.html`.

**LIVE ON gold bug on droplet:** see [DROPLET_SONU_ONAIR_PATCH.md](./DROPLET_SONU_ONAIR_PATCH.md) and paste [sonu-on-air-red.snippet.css](./sonu-on-air-red.snippet.css) if LIVE ON shows amber/gold instead of `#ff0000`.

## Production-safe update (droplet already has gold box / deposit / folds)

If the droplet already works, **do not** blind `git pull` a large unrelated branch. Prefer a **surgical** update:

1. Back up the live tree: `cp -a dashboard/templates/v1_only.html dashboard/templates/v1_only.html.bak`
2. If the server already has `paper_15m_engine.py`, **keep it** — do not replace with the repo stub:
   ```bash
   test -f paper_15m_engine.py && cp -a paper_15m_engine.py paper_15m_engine.py.bak
   ```
3. Merge or cherry-pick **only** Son U UI + small API helpers:
   - `dashboard/templates/v1_only.html` (gold box, ON-AIR, lamps, log tabs, **Paper run** fold — see [DROPLET_SONU_ONAIR_PATCH.md §8](./DROPLET_SONU_ONAIR_PATCH.md))
   - `dashboard/app.py` hunks for `_crypto15m_recent_trades`, optional `ledger=` query, `armed` / `wallet_gate` on control GET
4. Restore droplet engine if pull overwrote it: `mv paper_15m_engine.py.bak paper_15m_engine.py`
5. Restart dashboard proxy only: `sudo systemctl restart btc-5m-dashboard`

Son U JS still calls the same **`enable_live` / `disable_live` / pause / resume`** actions; **Go Live** and **Paper mode** buttons remain beside the lamp toggles.

API is backward-compatible: older `recent_trades(limit=…)` without `ledger` still works.

## Full deploy after merge to main

```bash
cd /root/btc-5m-bot
git pull origin main
sudo systemctl restart btc-5m-dashboard
sudo systemctl restart btc-5m-package2plus   # if Flask on :8083
```

Verify:

- **Live · Son U** tab: ON-AIR **LIVE ON** glows red when `mode=live`; near-black when Paper
- Green/yellow lamps on Live/Paper toggles; **Go Live** / **Paper mode** still work
- Live log / Paper log tabs; paper skipped collapsed by default
- `curl -s 'http://127.0.0.1:8082/api/crypto15m/control'` → `mode`, `armed`

No change to 5m V1 live flags, deposit logic, or unrelated dashboard panels in the Son U-only commits.
