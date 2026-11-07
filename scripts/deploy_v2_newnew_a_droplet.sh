#!/bin/bash
# Run ON droplet as root — creates son-btc-2legs-v2-newnew-a paper son
set -euo pipefail
ROOT=/root/btc-5m-bot
SON_ID=son-btc-2legs-v2-newnew-a
UNIT=btc-son-btc-2legs-v2-newnew-a.service
TS=$(TZ=Asia/Hong_Kong date +%Y%m%d-%H%M%S)
SESSION="paper-${SON_ID}-${TS}"
ENV_PATH="${ROOT}/configs/sons/${SON_ID}.env"
BAK="${ROOT}/bak-v2-newnew-a-${TS}"

mkdir -p "$BAK"
cp -a "${ROOT}/configs/sons/registry.json" "$BAK/" 2>/dev/null || true

cat > "$ENV_PATH" << EOF
# V2 newnew A · Strategy-A + one-leg abort — PAPER test · ${TS} HKT
# max_opp 0.55 + REQUIRE_OPP_BEFORE_LEG1 + opp-before ≤0.50 + ONE_LEG_GUARD abort
TRADING_MODE=paper
SON_ARMED=1
SON_ID=${SON_ID}
SON_DISPLAY_NAME=V2 newnew A · abort
SON_PARENT_FAMILY=BTC 2 legs V2
SON_VARIANT=NEWNEW-A
SON_CHANGE=Strategy-A paper: max_opp 0.55 + opp-before ≤0.50 + UNIT\$6 + ONE_LEG_GUARD abort (3 polls)
SON_INITIAL_CAPITAL_USD=100
SON_STATUS_PATH=${ROOT}/logs/sons/${SON_ID}_status.json
SON_TRADE_LOG=${ROOT}/logs/sons/${SON_ID}_trades.json
DUAL_LEG_LIVE_DRY_RUN=1
REAL_MONEY=0
SON_FAMILY=v2
SON_LETTER=NA
DUAL_LEG_UNIT_USD=6
DUAL_LEG_MAX_OPPOSITE_ASK=0.55
MAX_OPP_OVERRIDE=0.55
REQUIRE_OPP_BEFORE_LEG1=1
OPP_BEFORE_LEG1_MAX=0.50
ONE_LEG_GUARD=1
SON_ABORT_POLLS=3
SON_MAX_UNHEDGED=1
SON_LAUNCH=1
SON_SESSION_ID=${SESSION}
EOF

cat > "/etc/systemd/system/${UNIT}" << EOF
[Unit]
Description=V2 newnew A · abort PAPER (Strategy-A + one-leg abort · max_opp0.55)
After=network.target
Documentation=file://${ROOT}/configs/sons/OPS_NOTE.md

[Service]
Type=simple
WorkingDirectory=${ROOT}
EnvironmentFile=-${ROOT}/.env
EnvironmentFile=-${ENV_PATH}
Environment=PYTHONUNBUFFERED=1
Environment=SON_LAUNCH=1
ExecStart=${ROOT}/venv/bin/python ${ROOT}/scripts/son_paper_armed_daemon.py --son-id ${SON_ID}
Restart=always
RestartSec=8
MemoryMax=512M
MemorySwapMax=0

[Install]
WantedBy=multi-user.target
EOF

python3 << PY
import json
from pathlib import Path

root = Path("${ROOT}")
reg_path = root / "configs/sons/registry.json"
reg = json.loads(reg_path.read_text(encoding="utf-8"))
sons = reg.get("sons") or []
if any(str(s.get("id")) == "${SON_ID}" for s in sons):
    raise SystemExit("son already in registry")

entry = {
    "id": "${SON_ID}",
    "display_name": "V2 newnew A · abort",
    "family": "BTC 2 legs V2",
    "role": "son",
    "son": "NA",
    "paper_id": "${SON_ID}",
    "code": "Strategy-A paper: max_opp 0.55 + opp-before ≤0.50 + UNIT\$6 + ONE_LEG_GUARD abort",
    "session": "${SESSION}",
    "initial_capital_usd": 100.0,
    "status": "RUNNING · paper",
    "kind": "v2",
    "config_path": "${ENV_PATH}",
    "unit": "${UNIT}",
    "live_unit_enabled": False,
    "change": "newnew V2 Strategy-A + abort paper test · \$100",
    "knobs": {
        "SON_FAMILY": "v2",
        "SON_LETTER": "NA",
        "DUAL_LEG_UNIT_USD": "6",
        "DUAL_LEG_MAX_OPPOSITE_ASK": "0.55",
        "MAX_OPP_OVERRIDE": "0.55",
        "REQUIRE_OPP_BEFORE_LEG1": "1",
        "OPP_BEFORE_LEG1_MAX": "0.50",
        "ONE_LEG_GUARD": "1",
        "SON_ABORT_POLLS": "3",
        "SON_MAX_UNHEDGED": "1",
    },
    "launched": True,
    "armed": True,
    "remark": "newnew V2 · abort stack paper · NOT live",
}
# Insert after live-shadow if present, else after first V2 son
insert_at = len(sons)
for i, s in enumerate(sons):
    if str(s.get("id") or "") == "son-btc-2legs-v2-live-shadow":
        insert_at = i + 1
        break
    if str(s.get("family") or "") == "BTC 2 legs V2" and insert_at == len(sons):
        insert_at = i
sons.insert(insert_at, entry)
reg["sons"] = sons
reg_path.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("registry updated at index", insert_at)
PY

systemctl daemon-reload
systemctl enable --now "$UNIT"
sleep 4
echo "SESSION=${SESSION}"
