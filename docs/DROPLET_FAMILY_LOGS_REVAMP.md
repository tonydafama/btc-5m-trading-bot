# Droplet patch: Family Logs UI revamp (`/logs`)

Target: **143.110.240.132** (dashboard on `:8082`). Remote checkout: **`/root/btc-5m-bot/`**. Surgical update only — no full `git pull`, no live-trading engine changes.

## API contract (droplet — template assumes this)

### `GET /api/family-bots`

```json
{
  "ok": true,
  "n": 42,
  "bots": [
    {
      "id": "son-btc-5m-ultimate-live",
      "display_name": "終極5m · Live",
      "status": "紙上預備 · 跑緊",
      "mode": "paper",
      "paused": false,
      "running": true
    }
  ]
}
```

UI picks the bot with **`id === son-btc-5m-ultimate-live`** and renders **one** status line via `status` (or derives from `mode` / `paused` / `running`).

### `GET /api/bot-log-tail?id=…&mode=trades|all&n=…`

```json
{
  "stats": { "trades": 0, "skips": 30, "total": 30 },
  "source": "son-btc-5m-ultimate-live_trades.json",
  "lines": ["2026-11-02 18:00:04 … SKIP …", "…"]
}
```

- **`stats.trades`** = fill count (template also accepts legacy **`stats.fills`**).
- **`source`** = log file basename (legacy **`source_file`** still accepted).
- **`lines`** = array of **plain strings** (JSON objects still parsed if present).

## What changed in UI

- Mobile-first **`family_logs.html`**: Live Son U hero, default **全部**, one status line, high-contrast cards, tiny footer `source:` once.
- Helpers: **`parseLine`**, **`statusTag`**, **`renderRows`**, **`normalizeTailStats`**, **`findSonULiveBot`**, **`statusLineFromBot`**.

## Files to copy

From PR branch / local repo:

```bash
REMOTE=root@143.110.240.132
BASE=/root/btc-5m-bot

scp dashboard/templates/family_logs.html "$REMOTE:$BASE/dashboard/templates/family_logs.html"
```

Usually **HTML only** — droplet already exposes the APIs above.

## Restart

```bash
ssh root@143.110.240.132 'sudo systemctl restart btc-5m-dashboard'
```

## Verify

```bash
curl -s 'http://127.0.0.1:8082/api/family-bots' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('ok'), d.get('n'), [b.get('id') for b in (d.get('bots') or [])[:3]])"
curl -s 'http://127.0.0.1:8082/api/bot-log-tail?id=son-btc-5m-ultimate-live&mode=all&n=5' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('stats'), d.get('source'), type((d.get('lines') or [None])[0]).__name__)"
```

On phone: `http://143.110.240.132:8082/logs` → hard refresh. Expect **全部** selected, skip rows visible, single status line.

## Main Desk

Do **not** re-enable inline log tails on `/`. Log entry remains **Log → /logs** only.
