# Surgical deploy: Son U live truth + V2 high Live prep

Path: **`/root/btc-5m-bot/`** (Family Desk). No `DRY_RUN=0`, no go-live.

## Files (from PR #2 branch)

- `dashboard/sonu_live_truth.py` (new)
- `dashboard/app.py` — `/api/son-u/live-control`, truthful `_crypto15m_control_payload`
- `dashboard/paper_board.py` — `high_live_board` (+ V2 row), `v2_golive_prep`
- `dashboard/templates/v1_only.html` — gold box PREP vs LIVE ON, `#high-live-rows`, toggle → `postSonULive`

Droplet Family Desk may use a different main template filename; merge the same CSS/JS/API blocks if not byte-identical to `v1_only.html`.

## Restart

```bash
sudo systemctl restart btc-5m-dashboard
```

## Verify

```bash
curl -s http://127.0.0.1:8082/api/son-u/live-control | python3 -m json.tool | head -25
curl -s http://127.0.0.1:8082/api/all-paper-strategies | python3 -c "import sys,json;d=json.load(sys.stdin);h=d['high_live_board'];print(h['any_live'], [r['display_name'] for r in h['rows']])"
```

Expect: `any_live` false while exec DRY_RUN placeholder; toggle `to_live` → `live_armed_prep` true, still no `live_active`.
