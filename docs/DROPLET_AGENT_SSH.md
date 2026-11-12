# Droplet SSH for Cloud Agents

Host: `root@143.110.240.132` · repo on server: `/root/btc-5m-bot`

Agents use `scripts/droplet_ssh.sh` and `scripts/droplet_scp.sh`.

## Option A — Paste your existing private key (fastest)

1. In [Cloud Agent environment secrets](https://cursor.com/dashboard/cloud-agents/environments), add:

   - **`BTC_DROPLET_SSH_PRIVATE_KEY`** — full OpenSSH private key (PEM), newlines as `\n` or real newlines.

2. Optional: **`BTC_DROPLET_HOST`** (default `root@143.110.240.132`).

3. New agent boots run `scripts/setup_agent_ssh.sh` (via environment install) and write `~/.ssh/btc_droplet_ed25519`.

## Option B — Agent-generated key (one-time authorize)

Each agent VM may generate:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKohwnzrOdzlzo9pOm9l6wz+cjpqC82Wr3yFZP0a01Hc cursor-cloud-agent-btc-5m
```

On the droplet (console or your machine):

```bash
mkdir -p /root/.ssh && chmod 700 /root/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKohwnzrOdzlzo9pOm9l6wz+cjpqC82Wr3yFZP0a01Hc cursor-cloud-agent-btc-5m' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

Then agents on this VM can run:

```bash
./scripts/droplet_ssh.sh 'hostname'
./scripts/droplet_ssh.sh 'bash -s' < scripts/apply_midrange_vol_droplet.sh
```

## Verify

```bash
./scripts/droplet_ssh.sh 'systemctl is-active midrange-vol-live btc-5m-dashboard'
```
