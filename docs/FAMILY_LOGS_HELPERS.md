# family_logs.html — required JS helpers (do not drop)

File: `dashboard/templates/family_logs.html`  
Served at **`/logs`** when this file exists (see `dashboard/app.py` → `v1_logs`).

## Hotfix context (DO 143.110.240.132)

A Son U lock patch removed **`parseLine`**, **`statusTag`**, and **`renderRows`** → browser error **`renderRows is not defined`**.  
Restored from `bak-sonu-log-lock-20261102-193235`. This repo now ships all three **in full**.

## Invariant when editing Son U lock

When you change Son U filtering, folds, or API URLs on `/logs`:

1. **Keep** the block marked `CORE LOG HELPERS` intact (`parseLine`, `statusTag`, `renderRows`).
2. Son U lock code (`isSonuTradeRow`, `refreshAll`, …) must **call** `renderRows` — never inline a partial copy without the helpers.
3. **Main Desk** (`v1_only.html`): **no** log tails; users open logs via top-right **`Log → /logs`** only.

## Droplet surgical sync (no full pull)

```bash
cd /root/btc-5m-bot
cp -a dashboard/templates/family_logs.html dashboard/templates/family_logs.html.bak-$(date +%Y%m%d-%H%M)
# Merge ONLY from PR branch file, or diff helpers:
grep -n "function parseLine\\|function statusTag\\|function renderRows" dashboard/templates/family_logs.html
sudo systemctl restart btc-5m-dashboard
```

Verify `/logs` loads with no console errors; Live Son U section renders lines.

UX patch (成交 vs skip): [DROPLET_FAMILY_LOGS_UX.md](./DROPLET_FAMILY_LOGS_UX.md).
