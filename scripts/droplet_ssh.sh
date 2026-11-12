#!/usr/bin/env bash
# SSH wrapper for DigitalOcean btc-5m bot (143.110.240.132).
set -euo pipefail

DROPLET_HOST="${BTC_DROPLET_HOST:-root@143.110.240.132}"
KEY_PATH="${BTC_DROPLET_SSH_KEY_PATH:-$HOME/.ssh/btc_droplet_ed25519}"

_find_chat_upload_key() {
  local f dir
  for dir in \
    "/home/ubuntu/.cursor/projects/agent-repos-btc-5m-trading-bot/uploads" \
    "/workspace/uploads" \
    "$(dirname "$0")/../uploads"; do
    [ -d "$dir" ] || continue
  shopt -s nullglob
    for f in "$dir"/cursor_cloud_droplet_key*; do
      [ -f "$f" ] || continue
      echo "$f"
      return 0
    done
  shopt -u nullglob
  done
  return 1
}

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

# Same order as successful Nov 2–10 Family Desk deploys (chat attachment first).
upload_key="$(_find_chat_upload_key || true)"
if [ -n "$upload_key" ]; then
  KEY_PATH="$upload_key"
  chmod 600 "$KEY_PATH" 2>/dev/null || true
elif [ -n "${DROPLET_SSH_KEY:-}" ]; then
  tmp="${DROPLET_SSH_KEY_FILE:-/tmp/droplet_key_from_chat}"
  printf '%b' "$DROPLET_SSH_KEY" >"$tmp"
  chmod 600 "$tmp"
  KEY_PATH="$tmp"
elif [ ! -f "$KEY_PATH" ]; then
  _write_key_from_env "$KEY_PATH" || true
fi

if [ ! -f "$KEY_PATH" ]; then
  store="/cursor/stores/self/ssh/btc_droplet_ed25519"
  if [ -f "$store" ]; then
    cp "$store" "$KEY_PATH"
    chmod 600 "$KEY_PATH"
  fi
fi

if [ ! -f "$KEY_PATH" ]; then
  echo "No SSH key." >&2
  echo "  Attach uploads/cursor_cloud_droplet_key in this chat (same as Nov Family Desk deploys), or" >&2
  echo "  set Cloud Agent secret BTC_DROPLET_SSH_PRIVATE_KEY (cursor-cloud-droplet key)." >&2
  echo "  See docs/CURSOR_DROPLET_SSH_SETUP.md" >&2
  exit 2
fi

exec ssh \
  -i "$KEY_PATH" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=15 \
  "$DROPLET_HOST" \
  "$@"
