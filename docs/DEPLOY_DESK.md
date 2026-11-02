# Desk dashboard (:8082) deploy notes

Production serves the V1 desk at **port 8082** (proxy) → Flask **8083** (`dashboard/app.py`). The home page is `dashboard/templates/v1_only.html`.

## Deploy after merge

On the DigitalOcean droplet (typical path `/root/btc-5m-bot`):

```bash
cd /root/btc-5m-bot
git pull origin main
# If you use the project venv:
/root/btc-5m-bot/venv/bin/pip install -r requirements.txt 2>/dev/null || true
sudo systemctl restart btc-5m-dashboard   # proxy :8082
sudo systemctl restart btc-5m-package2plus   # if Flask app is a separate unit on :8083
sudo systemctl restart crypto-15m-observer     # Son U / 15m observer (optional)
```

Verify:

- `curl -s http://127.0.0.1:8082/ | head` shows `V1 Live Dashboard`
- Open **Live · Son U** tab: red **LIVE** or blue **PAPER** banner, green/yellow lamps on mode buttons
- `curl -s 'http://127.0.0.1:8082/api/crypto15m/control'` includes `mode`, `armed`, `wallet_gate`

## UI behaviour (this PR)

- **Live · Son U** gold box reflects `CRYPTO_15M_TRADING_MODE` and `/api/crypto15m/control` (not cosmetic-only).
- **Live log** / **Paper log** tabs call `/api/crypto15m/trades?ledger=live|paper`.
- Paper skipped rows hidden by default; checkbox expands fine-grained paper noise.
- `enable_live` still runs the existing go-live script when present; blocked by wallet gate (`CRYPTO_15M_MIN_WALLET_USD`, default 50) when wallet is below threshold.

No change to 5m V1 live trading flags in this PR—Son U (15m) clarity only.
