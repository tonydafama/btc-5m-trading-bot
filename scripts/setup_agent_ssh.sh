#!/usr/bin/env bash
# Materialize droplet SSH key from Cloud Agent secret (idempotent).
set -euo pipefail
DEST="${BTC_DROPLET_SSH_KEY_PATH:-$HOME/.ssh/btc_droplet_ed25519}"
mkdir -p "$(dirname "$DEST")"
chmod 700 "$(dirname "$DEST")"
if [ -f "$DEST" ]; then
  exit 0
fi
if [ -n "${BTC_DROPLET_SSH_PRIVATE_KEY:-}" ]; then
  printf '%b' "$BTC_DROPLET_SSH_PRIVATE_KEY" >"$DEST"
  chmod 600 "$DEST"
  echo "Wrote droplet SSH key to $DEST"
  exit 0
fi
if ssh-keygen -t ed25519 -f "$DEST" -N "" -C "cursor-cloud-agent-btc-5m" 2>/dev/null; then
  chmod 600 "$DEST"
  echo "Generated new agent key — add pubkey to droplet (see docs/DROPLET_AGENT_SSH.md):"
  ssh-keygen -y -f "$DEST"
fi
