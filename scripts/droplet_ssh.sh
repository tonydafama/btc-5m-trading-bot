#!/usr/bin/env bash
# SSH wrapper for DigitalOcean btc-5m bot (143.110.240.132).
set -euo pipefail

DROPLET_HOST="${BTC_DROPLET_HOST:-root@143.110.240.132}"
KEY_PATH="${BTC_DROPLET_SSH_KEY_PATH:-$HOME/.ssh/btc_droplet_ed25519}"

_write_key_from_env() {
  local dest="$1"
  local raw="${BTC_DROPLET_SSH_PRIVATE_KEY:-}"
  if [ -z "$raw" ]; then
    return 1
  fi
  mkdir -p "$(dirname "$dest")"
  chmod 700 "$(dirname "$dest")"
  # Support literal \\n in secret values.
  printf '%b' "$raw" >"$dest"
  chmod 600 "$dest"
  return 0
}

if [ ! -f "$KEY_PATH" ]; then
  _write_key_from_env "$KEY_PATH" || true
fi

if [ ! -f "$KEY_PATH" ]; then
  # Legacy agent upload path (older cloud runs).
  legacy="/home/ubuntu/.cursor/projects/agent-repos-btc-5m-trading-bot/uploads/cursor_cloud_droplet_key_fca7"
  if [ -f "$legacy" ]; then
    KEY_PATH="$legacy"
    chmod 600 "$KEY_PATH" 2>/dev/null || true
  fi
fi

if [ ! -f "$KEY_PATH" ]; then
  echo "No SSH key: set BTC_DROPLET_SSH_PRIVATE_KEY in Cloud Agent secrets or add pubkey from docs/DROPLET_AGENT_SSH.md" >&2
  exit 2
fi

exec ssh \
  -i "$KEY_PATH" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=15 \
  "$DROPLET_HOST" \
  "$@"
