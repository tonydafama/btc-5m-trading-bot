# Family Desk — remove hero status strip (2026-11-03 HKT)

## Removed from UI

- Header subtitle `#sub` under **Family Desk** h1.
- Hero bar `#strip` (`.strip`): Parent N RUNNING · Son N READY · 合計 PnL · 真倉 OFF · Son 未 launch · 打開 Logs 頁 →
- JS in `renderFamilies()` that updated `#p-run`, `#s-ready`, `#sum-pnl`, `#sub`.

## Kept

- Top-right **Logs** button (`#btn-logs` → `/logs`).
- `/logs` route and page unchanged.
- Son U live / V2 / go-live untouched.

## Verify (droplet)

```bash
curl -s http://127.0.0.1:8082/ | grep -E 'id="strip"|p-run|sum-pnl|Son 未 launch|打開 Logs 頁|合計 PnL' && echo FAIL || echo OK_no_hero_strip
curl -s http://127.0.0.1:8082/ | grep -c 'btn-logs'   # expect >= 1
```
