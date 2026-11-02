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

## 4. Live / Paper mode lamps (green ON = vivid #00ff00)

Paste into `v1_only.html` `<style>` (scoped to `#sonu-gold-box` only):

[sonu-mode-lamps-green.snippet.css](./sonu-mode-lamps-green.snippet.css)

- **ON (active mode):** `.mode-lamp.lamp-green` on the selected toggle — **#00ff00** + glow (match visual weight of red LIVE ON).
- **Inactive:** `.lamp-yellow` stays dim/soft; no strong glow.
- **JS (optional but recommended on Family Desk):** in `renderCrypto15m`, set inline `#00ff00` on lamps with class `lamp-green` (copy from PR `v1_only.html`).

Verify: Paper mode → **Paper 模擬** green lamp blazing; Live mode → **Live 實盤** green lamp blazing; the other button’s yellow lamp stays muted.

## 5. Ensure LIVE toggles class `is-on` (not gold `pill warn`)

In the same file, search JS for `c15m-on-air` / `renderCrypto15m` / `Son U`. When backend mode is live, the row must get class **`is-on`** (not only a gold `.pill`).

If Family Desk uses `#sonu-live-on-pill`, add to that element when live:

- classes: `is-on` (remove `warn` / `gold` / amber classes)
- `data-sonu-on-air="live"`

## 6. Reload dashboard (no full bot restart)

```bash
sudo systemctl restart btc-5m-dashboard
# or whatever unit serves :8082 → Flask :8083
curl -sI http://127.0.0.1:8082/ | head -3
```

Hard refresh browser (mobile: close tab, reopen).

## 7. Window funnel row (考慮／過濾／入場) — gold Live card only

**Do not** move this into Paper run; it belongs **only** in the top `#sonu-gold-box`.

1. Paste HTML (+ CSS) from [sonu-window-funnel.snippet.html](./sonu-window-funnel.snippet.html) inside `#sonu-gold-box`: **`#c15m-trades-windows-line`** (Trades `N 單 · M 窗 · 過濾 K`) **before** `.sonu-metrics`, then the 考慮／過濾／入場 funnel row.
2. Merge **JS** from PR `v1_only.html` `renderCrypto15m` (fills `c15m-trades-windows-line` + `c15m-win-*` from `summary.window_funnel`; `trades` = bot `trades_entered`, `considered` = windows from quality/log).
3. Ensure API returns counts — **prefer surgical `dashboard/app.py` only** (no engine overwrite):
   - Copy `_enrich_crypto15m_window_funnel` + `_window_funnel_from_trade_rows` from PR `app.py` onto `/api/crypto15m/summary`.
   - Or merge funnel helpers into droplet `paper_15m_engine.py` if you already maintain that file.

Quick check:

```bash
curl -s http://127.0.0.1:8082/api/crypto15m/summary | python3 -c "import sys,json; d=json.load(sys.stdin); wf=d.get('window_funnel') or {}; print('trades', wf.get('trades'), 'windows', wf.get('considered'), 'filtered', wf.get('filtered'), wf.get('ledger'))"
```

Mobile: gold box shows **`Trades N 單 · M 窗 · 過濾 K`** (when filtered known), plus **考慮 / 過濾 / 入場** funnel tiles.

## 8. Verify

- **Live (真倉 ON):** LIVE ON = **#ff0000** glow, red lamp — **not** gold/amber.
- **Paper:** LIVE ON area near-black / off.
- **Mode toggles:** active **Live 實盤** or **Paper 模擬** shows **#00ff00** lamp glow; inactive yellow lamp stays soft.
- Gold box shows **考慮 / 過濾 / 入場** counts for current ledger.
- WR / deposit / fold sections unchanged.

## 9. Paper run fold（全部紙上區預設摺起）

Surgical merge from PR `v1_only.html`:

- **Keep expanded on top:** single `#sonu-gold-box` (Live · Son U) + `#sonu-desk-panel` (control + logs).
- **Below that:** `<details id="paper-run-fold" class="paper-run-fold">` **without** `open` → default collapsed; summary title **Paper run**.
- **Inside `#paper-run-body`:** `#all-paper-board`, profile, paper tabs (Live 5m / Dual-Leg), `#paper-run-extra`.
- **Family Desk only:** mark each paper block (Sports FV, Grok Newborn, BTC families, 已停, 其他服務) with `data-paper-run-group` anywhere in the template; on load, JS moves them into `#paper-run-extra`.

Do **not** duplicate `#sonu-gold-box` inside tabs. Do **not** overwrite droplet `paper_15m_engine.py` unless you only add funnel helpers.

Verify mobile: only gold Live box + hero + Son U desk visible until user taps **Paper run**.

## 10. Son U logs — **/logs only** (not on main Desk)

Main `:8082/` **must not** show Live/Paper log tails on the Live · Son U card. See **[DROPLET_HIDE_SONU_LOG_ON_MAIN.md](./DROPLET_HIDE_SONU_LOG_ON_MAIN.md)** (incl. Family Desk `renderHighLive` / remove `sonuDualLog(...)`).

On **`/logs`** (`family_logs.html` on DO): keep Son U-only Live log + collapsed Paper + other bots; API `sonu_only=1` on `/api/crypto15m/trades` still applies there.

## Rollback

```bash
cp -a dashboard/templates/v1_only.html.bak-* dashboard/templates/v1_only.html
sudo systemctl restart btc-5m-dashboard
```
