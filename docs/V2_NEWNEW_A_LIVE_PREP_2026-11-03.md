# V2 newnew A · abort — Live prep (2026-11-03 HKT)

Droplet `143.110.240.132` · Family Desk `:8082` Live gold box.

**Live prep only — 真倉 OFF · 紙上 son keeps running.**

| Item | Value |
|------|--------|
| SON_ID | `son-btc-2legs-v2-newnew-a` |
| Paper unit | `btc-son-btc-2legs-v2-newnew-a.service` |
| Live prep unit | `dual-leg-live-v2-newnew-a.service` (**disabled**, DRY_RUN=1) |
| Live env | `config/v2_newnew_live_exec.env` |
| API | `GET/POST /api/v2-newnew/live-control` |

Wallet ~$100 USDC is **shared with Son U live** — real NA go-live needs more deposit or pausing Son U.

Apply on droplet: `python3 scripts/patch_v2_newnew_live_prep_droplet.py`

Agent: https://cursor.com/agents/bc-0f2de03a-7841-487a-89fa-a2ec1e7e7a71
