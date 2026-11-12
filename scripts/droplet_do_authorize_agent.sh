#!/usr/bin/env bash
# If DIGITALOCEAN_ACCESS_TOKEN is set, print droplet id for manual next step.
# (DO API cannot append authorized_keys on a running droplet — console or SSH still required.)
set -euo pipefail
TOKEN="${DIGITALOCEAN_ACCESS_TOKEN:-}"
if [ -z "$TOKEN" ]; then
  echo "Set DIGITALOCEAN_ACCESS_TOKEN in Cloud Agent secrets." >&2
  exit 2
fi
curl -sS -H "Authorization: Bearer $TOKEN" \
  "https://api.digitalocean.com/v2/droplets?per_page=200" |
  python3 -c "import sys,json;d=json.load(sys.stdin);\
[print(x['id'], x.get('name'), [n.get('ip_address') for n in x.get('networks',{}).get('v4',[])]) for x in d.get('droplets',[]) if any(n.get('ip_address')=='143.110.240.132' for n in x.get('networks',{}).get('v4',[]))]"
