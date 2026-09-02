#!/bin/bash
# Install BTC 5m bot systemd services. Does NOT touch weather-bot.
set -euo pipefail

ROOT="/root/btc-5m-bot"

echo "=== BTC 5m Bot Setup (Phase 0) ==="

if [ ! -d "$ROOT/venv" ]; then
  python3 -m venv "$ROOT/venv"
  "$ROOT/venv/bin/pip" install -q requests python-dotenv flask
fi

mkdir -p "$ROOT/logs"

cp "$ROOT/scripts/btc-5m-observer.service" /etc/systemd/system/
cp "$ROOT/scripts/btc-5m-dashboard.service" /etc/systemd/system/

systemctl daemon-reload
systemctl enable btc-5m-observer.service btc-5m-dashboard.service
systemctl restart btc-5m-observer.service btc-5m-dashboard.service

echo ""
echo "✓ Observer running (log only)"
echo "✓ Dashboard proxy on http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_IP'):8082"
echo ""
echo "Weather bot untouched at /root/weather-bot (:8080)"
echo "To remove: bash $ROOT/scripts/delete_btc_bot.sh"
