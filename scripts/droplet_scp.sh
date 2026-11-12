#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -lt 2 ]; then
  echo "Usage: droplet_scp.sh <local-file> <remote:/path>" >&2
  exit 1
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${BTC_DROPLET_SSH_KEY_PATH:-$HOME/.ssh/btc_droplet_ed25519}"
HOST="${BTC_DROPLET_HOST:-root@143.110.240.132}"
if [ ! -f "$KEY" ] && [ -n "${BTC_DROPLET_SSH_PRIVATE_KEY:-}" ]; then
  "$ROOT/scripts/droplet_ssh.sh" echo ok >/dev/null
  KEY="${BTC_DROPLET_SSH_KEY_PATH:-$HOME/.ssh/btc_droplet_ed25519}"
fi
exec scp -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$1" "$HOST:$2"
