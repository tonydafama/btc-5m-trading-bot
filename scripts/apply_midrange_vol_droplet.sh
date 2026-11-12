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
for kv in MIDVOL_PRIMARY_DASHBOARD=1 FAMILY_DEFAULT_LIVE_BOT=midrange-vol-live; do
  key="${kv%%=*}"
  if grep -q "^Environment=${key}=" "$OVERRIDE" 2>/dev/null; then
    sed -i "s|^Environment=${key}=.*|Environment=${kv}|" "$OVERRIDE"
  else
    printf '\nEnvironment=%s\n' "$kv" >>"$OVERRIDE"
  fi
done
# Pull latest midvol branch code when deploying from main checkout
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$ROOT" fetch origin cursor/midrange-vol-7a71 2>/dev/null || true
  git -C "$ROOT" checkout cursor/midrange-vol-7a71 2>/dev/null || true
  git -C "$ROOT" pull --ff-only origin cursor/midrange-vol-7a71 2>/dev/null || true
fi

systemctl restart btc-5m-dashboard.service
echo "OK midrange-vol live daemon (armed=0) + dashboard reset"
