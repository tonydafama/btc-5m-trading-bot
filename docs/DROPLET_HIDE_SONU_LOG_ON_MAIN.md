# Droplet surgical — hide Son U log tail on main Desk (:8082)

Target host example: **143.110.240.132** (DigitalOcean).  
**Do not** `git pull` the whole PR. **Do not** enable live trading. Patch HTML/JS only.

## What to achieve

- Main board **Live · Son U** gold card: stats only (LIVE ON, toggles, Trades/窗, WR/PnL, 單數/注碼/本金 on Family Desk).
- **No** 「Live log · Son U only」 block, **no** inline log tail, **no** Paper log fold on the home page.
- Full logs stay on **`/logs`** (`family_logs.html` on Family Desk, or `v1_detail.html` in this repo).

## A. This repo template (`v1_only.html`)

File: `/root/btc-5m-bot/dashboard/templates/v1_only.html`

1. **Delete** the whole `#sonu-desk-panel` block that contains `.sonu-log-card` / `#c15m-log-panel-live` / `#c15m-paper-log-fold` (if still present).
2. **Keep** `#sonu-gold-box` + optional `#sonu-desk-panel` trading control grid (no tables).
3. **Logs entry:** top-right **`Log → /logs`** only — **no** second log link on the gold card, **no** log tail on home.
4. **Remove JS** on main page: `paintC15mLogPanels`, `renderC15mTradeTable`, `sonuDualLog`, fetches to `/api/crypto15m/trades` inside `refresh()` — logs load only on `/logs`.
5. **Remove CSS** (main page only): `.sonu-live-log-block`, `.sonu-paper-log-fold`, `.paper-skips-toggle` if unused elsewhere.

### `/logs` template (`family_logs.html`)

When patching Son U lock on the logs page, **never remove** `parseLine`, `statusTag`, `renderRows` — see [FAMILY_LOGS_HELPERS.md](./FAMILY_LOGS_HELPERS.md). Copy the whole helper block from PR `family_logs.html` if unsure.

Restart: `sudo systemctl restart btc-5m-dashboard` · hard refresh mobile.

## B. Family Desk (production — `renderHighLive` / `sonuDualLog`)

If your droplet uses **Family Desk** (not byte-identical to PR `v1_only.html`):

```bash
cd /root/btc-5m-bot   # or your bot root
cp -a dashboard/templates/v1_only.html dashboard/templates/v1_only.html.bak-$(date +%Y%m%d-%H%M)
grep -n "sonuDualLog\|renderHighLive\|Live log · Son U only\|Live log · Son U" dashboard/templates/v1_only.html | head -30
```

1. In **`renderHighLive`** (or equivalent Son U card builder): **remove** the call to **`sonuDualLog(...)`** and any HTML string for live/paper log viewers under the stats grid.
2. **Delete** markup matching user screenshot: red-bordered log container, `▾ Live · Son U (只呢隻…)`, Paper log `▸` fold **on the main page only**.
3. **Do not edit** `/logs` template (`family_logs.html`) except to verify Son U lock still works there.
4. Ensure top bar **`Log → /logs`** remains; do **not** re-add inline log tail on the gold card.

## Verify

- Home `:8082/`: no SKIP tail, no 「Live log · Son U only」.
- `:8082/logs`: Son U Live log + collapsed Paper + other bots unchanged.

## Rollback

```bash
cp -a dashboard/templates/v1_only.html.bak-* dashboard/templates/v1_only.html
sudo systemctl restart btc-5m-dashboard
```
