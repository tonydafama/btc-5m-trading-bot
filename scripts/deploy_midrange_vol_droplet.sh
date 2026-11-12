#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
"$ROOT/scripts/setup_agent_ssh.sh"
"$ROOT/scripts/droplet_ssh.sh" bash -s <<'EOS'
set -euo pipefail
cd /root/btc-5m-bot
git fetch origin cursor/midrange-vol-7a71
git checkout cursor/midrange-vol-7a71
git pull --ff-only origin cursor/midrange-vol-7a71
bash scripts/apply_midrange_vol_droplet.sh
curl -sS 'http://127.0.0.1:8082/api/family-bots' | head -c 400
echo
EOS
