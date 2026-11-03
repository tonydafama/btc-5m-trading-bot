# V2 newnew A · abort — paper son deploy (2026-11-03 HKT)

Droplet: `143.110.240.132` · repo `/root/btc-5m-bot`

## Summary

| Field | Value |
|-------|-------|
| **SON_ID** | `son-btc-2legs-v2-newnew-a` |
| **Unit** | `btc-son-btc-2legs-v2-newnew-a.service` |
| **Display** | V2 newnew A · abort |
| **Family** | BTC 2 legs V2 |
| **Session** | `paper-son-btc-2legs-v2-newnew-a-20261103-115244` |
| **Capital** | $100 paper |
| **Deploy time (HKT)** | 2026-11-03 11:52:54 |

## Strategy stack (paper only)

- `MAX_OPP_OVERRIDE=0.55` · `DUAL_LEG_MAX_OPPOSITE_ASK=0.55`
- `REQUIRE_OPP_BEFORE_LEG1=1` · `OPP_BEFORE_LEG1_MAX=0.50`
- `DUAL_LEG_UNIT_USD=6`
- `ONE_LEG_GUARD=1` · `SON_ABORT_POLLS=3` · `SON_MAX_UNHEDGED=1`
- `TRADING_MODE=paper` · `DUAL_LEG_LIVE_DRY_RUN=1` · `REAL_MONEY=0`
- **Never** dual-leg-live · **never** `REAL_MONEY=1`

## Paths (droplet)

- Env: `/root/btc-5m-bot/configs/sons/son-btc-2legs-v2-newnew-a.env`
- Status: `/root/btc-5m-bot/logs/sons/son-btc-2legs-v2-newnew-a_status.json`
- Trades: `/root/btc-5m-bot/logs/sons/son-btc-2legs-v2-newnew-a_trades.json`
- Registry: `configs/sons/registry.json` (entry `son-btc-2legs-v2-newnew-a`)
- Deploy: `scripts/deploy_v2_newnew_a_droplet.sh`

## Acceptance checklist

| # | Check | Result |
|---|--------|--------|
| 1 | `systemctl is-active btc-son-btc-2legs-v2-newnew-a` → active | **PASS** |
| 2 | journal START PAPER max_opp=0.55, opp-before, ONE_LEG_GUARD | **PASS** |
| 3 | status JSON RUNNING · paper, capital 100, session new | **PASS** |
| 4 | registry knobs mirrored, launched/armed | **PASS** |
| 5 | Desk `/api/all-paper-strategies` includes V2 newnew son | **PASS** |
| 6 | Son U exec active; `dual-leg-live-v2` inactive | **PASS** |

Note: `/api/desk/bots` only exposes Son U + high-live V2 card IDs, not every registry son.

## Safety (unchanged)

- `btc-son-btc-5m-ultimate-live-exec`: active
- `dual-leg-live-v2`: inactive
- `btc-son-btc-2legs-v2-live-shadow`: active (unchanged)

## Agent

https://cursor.com/agents/bc-0f2de03a-7841-487a-89fa-a2ec1e7e7a71
