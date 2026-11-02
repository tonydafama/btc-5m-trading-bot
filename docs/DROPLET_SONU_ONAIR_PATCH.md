# Droplet surgical patch — Son U LIVE ON radio red (:8082)

Target file only: `/root/btc-5m-bot/dashboard/templates/v1_only.html`  
Do **not** replace the whole Desk template; patch ON-AIR styling only.

## 1. Backup

```bash
cd /root/btc-5m-bot
cp -a dashboard/templates/v1_only.html dashboard/templates/v1_only.html.bak-$(date +%Y%m%d-%H%M)
```

## 2. Find LIVE ON markup (Family Desk may differ)

```bash
grep -n "LIVE ON\|c15m-on-air\|sonu-live-on\|live-on-pill\|on-air-row" dashboard/templates/v1_only.html | head -20
```

Note the **id/class** on the LIVE ON element (e.g. `#c15m-on-air`, `#sonu-live-on-pill`, `.live-on-pill`).

## 3. Paste CSS before `</style>` (first `<style>` block in file)

Insert the block from `docs/sonu-on-air-red.snippet.css` in this repo, **or** copy from PR branch:

```bash
# From a machine with the PR checked out:
scp dashboard/templates/v1_only.html user@droplet:/tmp/v1_only.pr.html
# On droplet: merge only the ON-AIR CSS section manually, or:
grep -A200 'ON-AIR must never inherit gold' /tmp/v1_only.pr.html >> /tmp/onair.css
# Then paste /tmp/onair.css into v1_only.html before </style>
```

Minimal standalone snippet (works if your row id is `c15m-on-air`):

See [docs/sonu-on-air-red.snippet.css](./sonu-on-air-red.snippet.css).

## 4. Ensure LIVE toggles class `is-on` (not gold `pill warn`)

In the same file, search JS for `c15m-on-air` / `renderCrypto15m` / `Son U`. When backend mode is live, the row must get class **`is-on`** (not only a gold `.pill`).

If Family Desk uses `#sonu-live-on-pill`, add to that element when live:

- classes: `is-on` (remove `warn` / `gold` / amber classes)
- `data-sonu-on-air="live"`

## 5. Reload dashboard (no full bot restart)

```bash
sudo systemctl restart btc-5m-dashboard
# or whatever unit serves :8082 → Flask :8083
curl -sI http://127.0.0.1:8082/ | head -3
```

Hard refresh browser (mobile: close tab, reopen).

## 6. Window funnel row (考慮／過濾／入場)

If missing on droplet, add inside `#sonu-gold-box` (after metrics grid) the block with ids
`c15m-win-considered`, `c15m-win-filtered`, `c15m-win-entered` from PR `v1_only.html`, and ensure
`/api/crypto15m/summary` returns `window_funnel` (merge PR `paper_15m_engine.py` funnel helpers only,
or rely on droplet engine if it already exposes `window_funnel` / `quality.combined.windows`).

Quick check:

```bash
curl -s http://127.0.0.1:8082/api/crypto15m/summary | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('window_funnel'))"
```

## 7. Verify

- **Live (真倉 ON):** LIVE ON = **#ff0000** glow, red lamp — **not** gold/amber.
- **Paper:** LIVE ON area near-black / off.
- Gold box shows **考慮 / 過濾 / 入場** counts for current ledger.
- WR / deposit / fold sections unchanged.

## Rollback

```bash
cp -a dashboard/templates/v1_only.html.bak-* dashboard/templates/v1_only.html
sudo systemctl restart btc-5m-dashboard
```
