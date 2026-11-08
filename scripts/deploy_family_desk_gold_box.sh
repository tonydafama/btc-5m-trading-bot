#!/usr/bin/env bash
# Sync Family Desk gold-box fixes to the live droplet (key-only SSH).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${DROPLET_ROOT:-/root/btc-5m-bot}"
HOST="${DROPLET_HOST:-root@143.110.240.132}"
KEY="${DROPLET_KEY:-}"
STAMP="$(date +%Y%m%d-%H%M%S)"

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new)
SCP_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new)
if [[ -n "$KEY" && -f "$KEY" ]]; then
  SSH_OPTS+=(-i "$KEY" -o IdentitiesOnly=yes)
  SCP_OPTS+=(-i "$KEY" -o IdentitiesOnly=yes)
fi

ssh "${SSH_OPTS[@]}" "$HOST" "mkdir -p $ROOT/bak-gold-box-$STAMP && cp $ROOT/dashboard/paper_board.py $ROOT/dashboard/templates/v1_only.html $ROOT/bak-gold-box-$STAMP/ 2>/dev/null || true"

scp "${SCP_OPTS[@]}" "$REPO/dashboard/paper_board.py" "$HOST:$ROOT/dashboard/paper_board.py"
scp "${SCP_OPTS[@]}" "$REPO/dashboard/desk_bots.py" "$HOST:$ROOT/dashboard/desk_bots.py"
scp "${SCP_OPTS[@]}" "$REPO/dashboard/mode_books.py" "$HOST:$ROOT/dashboard/mode_books.py"
# Production uses v1_only.html; git source of truth is v1_only_droplet.html
scp "${SCP_OPTS[@]}" "$REPO/dashboard/templates/v1_only_droplet.html" "$HOST:$ROOT/dashboard/templates/v1_only.html"

ssh "${SSH_OPTS[@]}" "$HOST" "systemctl restart btc-5m-dashboard.service"
ssh "${SSH_OPTS[@]}" "$HOST" "curl -sf http://127.0.0.1:8083/api/all-paper-strategies | python3 -c \"import sys,json;d=json.load(sys.stdin);hl=d.get('high_live_board',{});print('wallet',hl.get('wallet_cash_usdc'));print('rows',len(hl.get('rows')or[]));print('books',sum(1 for x in (hl.get('rows')or[]) if x.get('mode_books')))\""

echo "OK: gold box deployed (backup bak-gold-box-$STAMP on droplet)"
