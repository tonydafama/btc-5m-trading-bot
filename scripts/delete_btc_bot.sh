#!/bin/bash
# Remove BTC 5m bot completely. Does NOT touch weather-bot.
set -euo pipefail

echo "=== Deleting BTC 5m Bot (weather-bot will NOT be affected) ==="

# Stop and disable BTC services only
for svc in btc-5m-observer btc-5m-dashboard; do
  if systemctl is-active --quiet "$svc.service" 2>/dev/null; then
    systemctl stop "$svc.service"
    echo "Stopped $svc"
  fi
  if systemctl is-enabled --quiet "$svc.service" 2>/dev/null; then
    systemctl disable "$svc.service"
    echo "Disabled $svc"
  fi
done

# Remove systemd unit files
rm -f /etc/systemd/system/btc-5m-observer.service
rm -f /etc/systemd/system/btc-5m-dashboard.service
systemctl daemon-reload

# Kill any stray processes on BTC ports only
for port in 8082 8083; do
  pid=$(lsof -ti :"$port" 2>/dev/null || true)
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    echo "Killed process on port $port"
  fi
done

# Remove BTC bot directory
if [ -d /root/btc-5m-bot ]; then
  rm -rf /root/btc-5m-bot
  echo "Removed /root/btc-5m-bot"
fi

# Verify weather bot still running
if systemctl is-active --quiet weather-bot-dashboard.service 2>/dev/null; then
  echo "✓ Weather dashboard still running on :8080"
else
  echo "ℹ Weather dashboard status: $(systemctl is-active weather-bot-dashboard.service 2>/dev/null || echo 'unknown')"
fi

echo ""
echo "✓ BTC bot fully removed. Weather bot at /root/weather-bot is untouched."
