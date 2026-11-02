# Droplet patch: Family Logs UI revamp (`/logs`)

Target: **143.110.240.132** (dashboard on `:8082`). Surgical update only — no full `git pull`, no live-trading engine changes.

## What changed

- Mobile-first **`family_logs.html`**: Live Son U hero, default **全部**, one status line, high-contrast log cards, tiny footer source line.
- Optional API helpers on droplet if not already deployed:
  - `GET /api/bot-log-tail?id=…&mode=trades|all&n=…` (`id` alias of `bot`, `n` alias of `limit`)
  - `GET /api/family-bots` (status line for Son U Live)
- **`bot_log_tail.py`**: also resolves `logs/sons/{bot}_trades.json`.

## Files to copy

From your dev machine / PR branch:

```bash
scp dashboard/templates/family_logs.html root@143.110.240.132:/root/btc-5m-trading-bot/dashboard/templates/family_logs.html
```

If the droplet does not yet have `id=` / `n=` or `/api/family-bots`:

```bash
scp dashboard/app.py root@143.110.240.132:/root/btc-5m-trading-bot/dashboard/app.py
scp bot_log_tail.py root@143.110.240.132:/root/btc-5m-trading-bot/bot_log_tail.py
```

Adjust remote path if your checkout lives elsewhere (e.g. `/home/ubuntu/...`).

## Restart

```bash
ssh root@143.110.240.132 'sudo systemctl restart btc-5m-dashboard'
```

## Verify

```bash
curl -s 'http://127.0.0.1:8082/api/family-bots' | head -c 400
curl -s 'http://127.0.0.1:8082/api/bot-log-tail?id=son-btc-5m-ultimate-live&mode=all&n=5' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('stats'), d.get('source_file'))"
```

On phone: open `http://143.110.240.132:8082/logs` → hard refresh. Expect **全部** selected, skip rows visible, single status line, no duplicate empty copy.

## Main Desk

Do **not** re-enable inline log tails on `/`. Log entry remains **Log → /logs** only.
