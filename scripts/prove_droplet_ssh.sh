#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec ./scripts/droplet_ssh.sh 'echo SSH_OK; hostname; curl -s http://127.0.0.1:8082/ | grep -oE "<title>[^<]+</title>" | head -1'
