#!/usr/bin/env bash
# Apply items 4,7,9 env knobs and restart live + dashboard (run on droplet or via ssh).
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

upsert DUAL_LEG_LIVE_DECISION_CLOCK paper_checkpoints
upsert DUAL_LEG_LIVE_REQUIRE_PATH_HEDGE 1
upsert REQUIRE_OPP_BEFORE_LEG1 1
upsert OPP_BEFORE_LEG1_MAX 0.50
upsert DUAL_LEG_LIVE_MAX_OPPOSITE_ASK 0.65
upsert DUAL_LEG_LIVE_UNIT_USD 1

OVERRIDE=/etc/systemd/system/dual-leg-live-v2.service.d/override.conf
if [[ -f "$OVERRIDE" ]]; then
  if grep -q DUAL_LEG_LIVE_DECISION_CLOCK "$OVERRIDE"; then
    sed -i 's|^Environment=DUAL_LEG_LIVE_DECISION_CLOCK=.*|Environment=DUAL_LEG_LIVE_DECISION_CLOCK=paper_checkpoints|' "$OVERRIDE"
  else
    printf '\nEnvironment=DUAL_LEG_LIVE_DECISION_CLOCK=paper_checkpoints\n' >>"$OVERRIDE"
  fi
fi

systemctl daemon-reload
systemctl restart dual-leg-live-v2.service
systemctl restart btc-5m-dashboard.service
echo "OK: paper_checkpoints + path hedge gate env applied"
