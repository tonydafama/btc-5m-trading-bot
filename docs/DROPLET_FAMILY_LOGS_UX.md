# Droplet surgical — Family Logs UX (:8082 /logs)

Host example: **143.110.240.132**. No full PR pull.

## Files

| File | Change |
|------|--------|
| `dashboard/templates/family_logs.html` | Mobile log cards, 成交/全部, empty state, seat labels |
| `bot_log_tail.py` | **New or patch** — trades mode returns fills only (no skip fallback) |
| `dashboard/app.py` | Route `GET /api/bot-log-tail?bot=…&mode=trades|all` (small add if missing) |

## Must keep (regression)

`parseLine`, `statusTag`, `renderRows` in `family_logs.html` — see [FAMILY_LOGS_HELPERS.md](./FAMILY_LOGS_HELPERS.md).

## UX checks after patch

1. **成交** selected → list has **no SKIP** rows; if fills=0 show `暫無成交（N 次 skip）· 撳「全部」睇過濾`.
2. Status shows **`Live 席 · 紙上預備`** (not plain `RUNNING · paper` alone).
3. **全部** shows skip rows with muted **SKIP** tags; **FILL** green for wins/losses.
4. Main Desk home still **no** log tail — **Log → /logs** only.

```bash
curl -s 'http://127.0.0.1:8082/api/bot-log-tail?bot=son-btc-5m-ultimate-live&mode=trades' | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('stats'), len(d.get('lines') or []))"
sudo systemctl restart btc-5m-dashboard
```
