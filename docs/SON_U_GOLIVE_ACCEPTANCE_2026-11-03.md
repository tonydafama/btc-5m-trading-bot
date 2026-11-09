# Son U go-live acceptance — 2026-11-03 HKT

Template for droplet copy at `/root/btc-5m-bot/docs/SON_U_GOLIVE_ACCEPTANCE_2026-11-03.md`.

## Checks

| Check | Expected |
|-------|----------|
| `GET /api/son-u/live-control` | `mode: live`, `live_active: true`, `exec_dry_run_placeholder: false`, `exec_is_real_live: true`, `wallet_ok_for_live: true` |
| `GET /api/desk/bots` Son U | `stake_usd: "10"`, `capital_usd` ≈ wallet (~100.52) |
| Family Desk UI | Son U expanded; V2 in `<details class="desk-v2-fold">` (collapsed) |
| `btc-son-btc-5m-ultimate-live-exec` | **active** |
| `btc-son-btc-5m-ultimate-live` (paper prep) | **inactive/disabled** (no double trade) |
| `dual-leg-live-v2` | **inactive** |

## Commands

```bash
curl -s http://127.0.0.1:8082/api/son-u/live-control | python3 -m json.tool
curl -s http://127.0.0.1:8082/api/desk/bots | python3 -m json.tool
systemctl is-active btc-son-btc-5m-ultimate-live-exec btc-son-btc-5m-ultimate-live dual-leg-live-v2
grep -c 'desk-v2-fold' /root/btc-5m-bot/dashboard/templates/v1_only.html
```
