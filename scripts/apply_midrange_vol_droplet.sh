#!/usr/bin/env bash
# Deploy midrange-vol strategy, reset dashboard session, stop legacy live bots.
set -euo pipefail
ROOT="${DROPLET_ROOT:-/root/btc-5m-bot}"
ENV="$ROOT/config/midrange_vol_live.env"
mkdir -p "$ROOT/config"

cat >"$ENV" <<'EOF'
MIDVOL_TRADING_MODE=live
MIDVOL_LIVE_ARMED=0
MIDVOL_DRY_RUN=0
MIDVOL_STAKE_USD=3
MIDVOL_MIN_PRIOR_RANGE_USD=80
MIDVOL_T_MIN=40
MIDVOL_T_MAX=75
MIDVOL_PRIMARY_DASHBOARD=1
EOF

# Stop dual-leg / old BTC live observers on same wallet
for u in dual-leg-live-v2 btc-5m-observer; do
  systemctl stop "$u" 2>/dev/null || true
  systemctl disable "$u" 2>/dev/null || true
done

"$ROOT/venv/bin/python" "$ROOT/scripts/reset_midrange_vol_dashboard.py"

install -m 644 "$ROOT/scripts/midrange-vol-live.service" /etc/systemd/system/midrange-vol-live.service
systemctl daemon-reload
systemctl enable midrange-vol-live.service
systemctl restart midrange-vol-live.service

# Dashboard env for / → /midvol
DASH_ENV="$ROOT/config/dashboard.env"
if grep -q MIDVOL_PRIMARY_DASHBOARD "$DASH_ENV" 2>/dev/null; then
  sed -i 's|^MIDVOL_PRIMARY_DASHBOARD=.*|MIDVOL_PRIMARY_DASHBOARD=1|' "$DASH_ENV"
else
  echo "MIDVOL_PRIMARY_DASHBOARD=1" >>"$DASH_ENV"
fi

OVERRIDE=/etc/systemd/system/btc-5m-dashboard.service.d/override.conf
mkdir -p "$(dirname "$OVERRIDE")"
if grep -q MIDVOL_PRIMARY_DASHBOARD "$OVERRIDE" 2>/dev/null; then
  sed -i 's|^Environment=MIDVOL_PRIMARY_DASHBOARD=.*|Environment=MIDVOL_PRIMARY_DASHBOARD=1|' "$OVERRIDE"
else
  printf '\nEnvironment=MIDVOL_PRIMARY_DASHBOARD=1\n' >>"$OVERRIDE"
fi

systemctl restart btc-5m-dashboard.service
echo "OK midrange-vol live daemon (armed=0) + dashboard reset"
