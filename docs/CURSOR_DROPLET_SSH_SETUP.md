# Cursor cloud → droplet (143.110.240.132)

## Auth (no password)

Private key file is attached in this Cloud Agent chat as `uploads/cursor_cloud_droplet_key` (comment **cursor-cloud-droplet**, already in droplet `authorized_keys`).  
Alternatives: Cloud Agent secret **`BTC_DROPLET_SSH_PRIVATE_KEY`**, or env **`DROPLET_SSH_KEY`**.

```bash
KEY="${DROPLET_SSH_KEY_FILE:-$(ls uploads/cursor_cloud_droplet_key* 2>/dev/null | head -1)}"
if [ -n "$DROPLET_SSH_KEY" ] && [ ! -f "$KEY" ]; then
  printf '%b' "$DROPLET_SSH_KEY" > /tmp/droplet_key
  KEY=/tmp/droplet_key
fi
chmod 600 "$KEY"
./scripts/droplet_ssh.sh 'echo SSH_OK'
```

## Live Desk truth (phone)

- URL: http://143.110.240.132:8082/
- Midvol: http://143.110.240.132:8082/logs · http://143.110.240.132:8082/midvol
- NEVER assume GitHub PR alone updates the droplet — run `scripts/apply_midrange_vol_droplet.sh` on the server.

## Deploy rule

1. SSH with the key above (`./scripts/prove_droplet_ssh.sh`)
2. On droplet: `cd /root/btc-5m-bot && git fetch && git checkout cursor/midrange-vol-7a71 && git pull && bash scripts/apply_midrange_vol_droplet.sh`
