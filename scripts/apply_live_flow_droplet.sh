#!/usr/bin/env bash
# Loosen live gates so entries can actually post in volatile books (run on droplet).
set -euo pipefail
ROOT="${DROPLET_ROOT:-/root/btc-5m-bot}"
ENV="$ROOT/config/v2_parent_live_exec.env"

upsert() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV"
  else
    echo "${key}=${val}" >>"$ENV"
  fi
}

# Keep timing + anti-strand; remove Rg80 chop pause and widen fills for FOK.
upsert DUAL_LEG_LIVE_DECISION_CLOCK paper_checkpoints
upsert DUAL_LEG_LIVE_REQUIRE_PATH_HEDGE 1
upsert DUAL_LEG_LIVE_RG80 0
upsert REQUIRE_OPP_BEFORE_LEG1 1
upsert OPP_BEFORE_LEG1_MAX 0.65
upsert DUAL_LEG_LIVE_MAX_OPPOSITE_ASK 0.68
upsert DUAL_LEG_LIVE_MAX_ASK 0.55
upsert DUAL_LEG_LIVE_UNIT_USD 3

OVERRIDE=/etc/systemd/system/dual-leg-live-v2.service.d/override.conf
if [[ -f "$OVERRIDE" ]]; then
  for pair in \
    "DUAL_LEG_LIVE_UNIT_USD=3" \
    "REQUIRE_OPP_BEFORE_LEG1=1" \
    "DUAL_LEG_LIVE_RG80=0"; do
    key="${pair%%=*}"
    if grep -q "^Environment=${key}=" "$OVERRIDE"; then
      sed -i "s|^Environment=${key}=.*|Environment=${pair}|" "$OVERRIDE"
    else
      printf '\nEnvironment=%s\n' "$pair" >>"$OVERRIDE"
    fi
  done
fi

systemctl daemon-reload
systemctl restart dual-leg-live-v2.service
systemctl restart btc-5m-dashboard.service
echo "OK: flow mode (no Rg80, max_ask=0.55, opp<=0.65, unit=\$3)"
