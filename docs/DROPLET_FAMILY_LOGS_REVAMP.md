# Droplet patch: Family Logs UI (`/logs`)

Target: **143.110.240.132** (dashboard on `:8082`). Remote checkout: **`/root/btc-5m-bot/`**. Surgical update only — no full `git pull`, no live-trading engine changes.

## API contract

### `GET /api/family-bots`

Minimal roster for the log page: Parent V2 live + Parent V2 paper.

```json
{
  "ok": true,
  "n": 2,
  "bots": [
    {
      "id": "dual-leg-live-v2",
      "display_name": "BTC 2 legs V2 · Live",
      "status": "LIVE ON · BTC 2 legs V2",
      "status_line": "LIVE ON · BTC 2 legs V2",
      "mode": "live",
      "live": true,
      "live_enabled": true,
      "running": true
    },
    {
      "id": "dual-leg-paper-v2",
      "display_name": "BTC 2 legs V2 · Paper",
      "status": "Parent V2 paper · 跑緊 · paper-v2-…",
      "mode": "paper",
      "running": true
    }
  ]
}
```

The UI uses **`id === dual-leg-live-v2`** for the hero “LIVE ON” title.

### `GET /api/dual-leg-log-tail?ledger=live|paper&mode=all|trades&n=40`

- **live** → tail of `logs/dual_leg_live_daemon.jsonl` (daemon events).
- **paper** → tail of `logs/dual_leg_btc_paper_trades.json` (observer paper rows).

```json
{
  "bot": "dual-leg-live-v2",
  "ledger": "live",
  "mode": "all",
  "stats": {
    "settles": 2,
    "posts": 5,
    "skips": 1,
    "fills": 7,
    "trades": 2,
    "info": 1,
    "total": 40
  },
  "source": "dual_leg_live_daemon.jsonl",
  "lines": [ { "event": "entry", "timestamp_hkt": "…", "slug": "…" } ]
}
```

- **Live**: **`settles`** = `trade_settled` rows (one per closed trade, **PnL 在呢度**); **`posts`** = `entry` + `hedge` FOK posts; **`skips`** = `skip_*` / failed posts; **`total`** = lines returned (tail).
- **Paper**: **`trades`** = filled/settled rows; **`skips`** = SKIP rows.

### Legacy: `GET /api/bot-log-tail?…`

Still used elsewhere; `/logs` no longer depends on it.

## UI (`family_logs.html`)

- Hero: **BTC 2 legs V2 · Live** — turns **LIVE ON · BTC 2 legs V2** when status contains `LIVE ON`.
- **Paper**: collapsible section, Parent V2 paper trades only.
- Helpers preserved: **`parseLine`**, **`statusTag`**, **`renderRows`**, **`normalizeTailStats`**.

## Files to copy

```bash
REMOTE=root@143.110.240.132
BASE=/root/btc-5m-bot

scp dashboard/templates/family_logs.html "$REMOTE:$BASE/dashboard/templates/family_logs.html"
scp dashboard/family_dual_leg_logs.py "$REMOTE:$BASE/dashboard/family_dual_leg_logs.py"
scp dashboard/app.py "$REMOTE:$BASE/dashboard/app.py"
```

## Restart

```bash
ssh root@143.110.240.132 'sudo systemctl restart btc-5m-dashboard'
```

## Verify

```bash
curl -s 'http://127.0.0.1:8082/api/family-bots' | python3 -c "import sys,json;d=json.load(sys.stdin);print([b.get('id') for b in d.get('bots') or []])"
curl -s 'http://127.0.0.1:8082/api/dual-leg-log-tail?ledger=live&mode=all&n=5' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('source'), len(d.get('lines') or []))"
curl -s 'http://127.0.0.1:8082/api/dual-leg-log-tail?ledger=paper&mode=all&n=5' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('source'), len(d.get('lines') or []))"
```

On phone: `http://143.110.240.132:8082/logs` → hard refresh.

## Main Desk

Log entry remains **Log → /logs** only.
