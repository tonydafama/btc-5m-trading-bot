#!/usr/bin/env python3
"""Apply V2 newnew A live-prep on droplet: registry, systemd, paper_board, docs."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path("/root/btc-5m-bot")
SON_ID = "son-btc-2legs-v2-newnew-a"
LIVE_UNIT = "dual-leg-live-v2-newnew-a.service"
PAPER_UNIT = "btc-son-btc-2legs-v2-newnew-a.service"


def patch_registry() -> None:
    reg_path = ROOT / "configs" / "sons" / "registry.json"
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    for p in reg.get("parents") or []:
        if isinstance(p, dict) and p.get("id") == "parent-btc-2legs-v2":
            p.pop("desk_section", None)
            p["note"] = (p.get("note") or "") + " · Live gold box → NA newnew prep"
    for s in reg.get("sons") or []:
        if not isinstance(s, dict) or s.get("id") != SON_ID:
            continue
        s.update(
            {
                "desk_section": "high_live",
                "go_live_ready": True,
                "live_prep": True,
                "live_tag": "go-live READY",
                "live_unit": LIVE_UNIT,
                "live_unit_enabled": False,
                "remark": "Strategy-A + abort · paper RUNNING · live-prep in gold box · NOT live",
                "change": "Live預備 · max_opp0.55 + opp-before0.50 + abort · 真倉 OFF",
            }
        )
        break
    else:
        raise SystemExit(f"{SON_ID} not in registry sons")
    reg_path.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_live_env() -> None:
    env_dir = ROOT / "config"
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "v2_newnew_live_exec.env").write_text(
        """# V2 newnew A live-exec prep — DRY_RUN only until explicit go-live checklist
TRADING_MODE=paper
REAL_MONEY=0
DRY_RUN=1
DUAL_LEG_LIVE_DRY_RUN=1
DUAL_LEG_LIVE_PROFILE=v2_live_shadow
DUAL_LEG_LIVE_UNIT_USD=6
DUAL_LEG_MAX_OPPOSITE_ASK=0.55
MAX_OPP_OVERRIDE=0.55
REQUIRE_OPP_BEFORE_LEG1=1
OPP_BEFORE_LEG1_MAX=0.50
ONE_LEG_GUARD=1
SON_ABORT_POLLS=3
SON_MAX_UNHEDGED=1
DUAL_LEG_LIVE_SESSION_ID=live-newnew-a-armed-not-started
DUAL_LEG_LIVE_STATE_PATH=/root/btc-5m-bot/logs/newnew_a_live/dual_leg_live_state.json
DUAL_LEG_LIVE_TRADES_PATH=/root/btc-5m-bot/logs/newnew_a_live/dual_leg_live_trades.json
DUAL_LEG_LIVE_STATUS_PATH=/root/btc-5m-bot/logs/newnew_a_live/dual_leg_live_status.json
DUAL_LEG_LIVE_EVENT_LOG=/root/btc-5m-bot/logs/newnew_a_live/dual_leg_live_daemon.jsonl
""",
        encoding="utf-8",
    )


def write_systemd_unit() -> None:
    unit_path = Path(f"/etc/systemd/system/{LIVE_UNIT}")
    unit_path.write_text(
        f"""[Unit]
Description=V2 newnew A · abort LIVE path (DISABLED · DRY_RUN=1) — enable only after deposit + Son U conflict resolved + go-live word
After=network.target
Documentation=file://{ROOT}/docs/V2_NEWNEW_A_LIVE_PREP_2026-11-03.md

[Service]
Type=simple
WorkingDirectory={ROOT}
EnvironmentFile=-{ROOT}/.env
EnvironmentFile=-{ROOT}/config/v2_newnew_live_exec.env
Environment=PYTHONUNBUFFERED=1
Environment=DUAL_LEG_LIVE_DRY_RUN=1
ExecStart={ROOT}/venv/bin/python {ROOT}/scripts/dual_leg_live_daemon.py --poll-sec 8
Restart=always
RestartSec=8
MemoryMax=512M
MemorySwapMax=0

[Install]
WantedBy=multi-user.target
""",
        encoding="utf-8",
    )


def patch_paper_board() -> None:
    pb = ROOT / "dashboard" / "paper_board.py"
    text = pb.read_text(encoding="utf-8")
    if "_na_high_live_finalize" in text and 'in ("dual-leg-live-v2", "son-btc-2legs-v2-newnew-a")' in text:
        return
    needle = '        rows_out.append({\n            "id": sid,'
    if needle not in text:
        raise SystemExit("paper_board append block not found")
    insert = '''        _row = {
            "id": sid,'''
    # Replace first rows_out.append({ with _row = { for the block - too fragile
    # Instead inject after rows_out.append block closing `})`  - use marker

    if "_na_high_live_finalize" not in text:
        helper = '''

def _na_high_live_finalize(root: Path, meta: dict, row: dict) -> dict:
    """V2 newnew A in Live gold box — prep labels, never fake 真倉 ON."""
    if str(meta.get("id") or row.get("id")) != "son-btc-2legs-v2-newnew-a":
        return row
    armed_prep = False
    try:
        from v2_newnew_live_truth import live_armed_prep as _na_armed
        armed_prep = bool(_na_armed(root))
    except Exception:
        pass
    row = dict(row)
    row["family"] = "BTC 2 legs V2"
    row["desk_section"] = "high_live"
    row["live_tag"] = meta.get("live_tag") or "go-live READY"
    row["live_prep"] = True
    row["go_live_ready"] = bool(meta.get("go_live_ready"))
    row["mode"] = "prep"
    row["live_enabled"] = False
    row["dry_run"] = True
    if armed_prep:
        row["status_label"] = "Live預備 · 紙上 armed · 真倉 OFF"
        row["change"] = meta.get("change") or "Strategy-A+abort · armed prep"
    else:
        row["status_label"] = "Live預備 · 紙上跑緊 · 真倉 OFF"
        row["change"] = meta.get("change") or "Strategy-A+abort · live-prep"
    row["note"] = meta.get("remark") or row.get("note") or ""
    return row

'''
        anchor = "def _high_live_board(root: Path) -> dict[str, Any]:"
        if anchor not in text:
            raise SystemExit("high_live_board anchor missing")
        text = text.replace(anchor, helper + anchor, 1)

    # Wrap append: find pattern rows_out.append({ ... "change": meta.get("change")
    old = '"change": meta.get("change") or meta.get("code") or st.get("note") or "",\n        })'
    new = '"change": meta.get("change") or meta.get("code") or st.get("note") or "",\n        })\n        rows_out[-1] = _na_high_live_finalize(root, meta, rows_out[-1])'
    if old in text and new not in text:
        text = text.replace(old, new, 1)

    old_fb = 'if not any(str(r.get("id") or "") == "dual-leg-live-v2" for r in rows_out):'
    new_fb = 'if not any(str(r.get("id") or "") in ("dual-leg-live-v2", "son-btc-2legs-v2-newnew-a") for r in rows_out):'
    text = text.replace(old_fb, new_fb)

    old_dec = 'if _rid in ("son-btc-5m-ultimate-live", "dual-leg-live-v2"):'
    new_dec = 'if _rid in ("son-btc-5m-ultimate-live", "dual-leg-live-v2", "son-btc-2legs-v2-newnew-a"):'
    text = text.replace(old_dec, new_dec)

    pb.write_text(text, encoding="utf-8")


def patch_app_route() -> None:
    app_path = ROOT / "dashboard" / "app.py"
    text = app_path.read_text(encoding="utf-8")
    if "/api/v2-newnew/live-control" in text:
        return
    block = '''

@app.route("/api/v2-newnew/live-control", methods=["GET", "POST"])
def api_v2_newnew_live_control():
    """V2 newnew A live-prep — arms paper prep only; never enables CLOB from Desk."""
    from v2_newnew_live_truth import apply_live_control_action, live_control_payload

    def _wallet():
        try:
            from scripts.dual_leg_live_daemon import _wallet_cash  # type: ignore
            return _wallet_cash()
        except Exception:
            return None

    wallet = _wallet()
    if request.method == "GET":
        return jsonify(live_control_payload(ROOT, wallet_cash_usdc=wallet))

    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "status").lower()
    out = apply_live_control_action(ROOT, action, wallet_cash_usdc=wallet)
    try:
        _all_paper_cache["ts"] = 0.0
        _all_paper_cache["data"] = None
        _refresh_all_paper_cache(force=True)
    except Exception:
        pass
    return jsonify(out), (200 if out.get("ok", True) else 400)

'''
    anchor = '@app.route("/api/son-u/live-control"'
    if anchor not in text:
        raise SystemExit("son-u route anchor missing")
    text = text.replace(anchor, block + anchor, 1)
    app_path.write_text(text, encoding="utf-8")


def write_doc() -> None:
    doc = ROOT / "docs" / "V2_NEWNEW_A_LIVE_PREP_2026-11-03.md"
    doc.write_text(
        """# V2 newnew A · abort — Live prep (2026-11-03 HKT)

**狀態：Live 預備完成 · 真倉 OFF · 紙上 son 繼續跑**

## 身份

| 項目 | 值 |
|------|-----|
| SON_ID | `son-btc-2legs-v2-newnew-a` |
| 紙上 unit | `btc-son-btc-2legs-v2-newnew-a.service`（保持 **active**） |
| Live prep unit | `dual-leg-live-v2-newnew-a.service`（**disabled + inactive**） |
| Desk | Family Desk `:8082` · **Live 金框** `high_live_board` |

## 策略 knob（紙上 = live prep 目標）

- max_opp **0.55** · REQUIRE_OPP_BEFORE_LEG1 · opp-before ≤ **0.50**
- ONE_LEG_GUARD **1** · abort_polls **3** · max_unhedged **1**
- unit **$6** · 紙本金 **$100**

## 錢包衝突（必讀）

- Polymarket 交易錢包約 **$100** 已由 **Son U 真倉**使用。
- 本 prep **唔會**落 CLOB、**唔會** `REAL_MONEY=1`。
- 真要 NA go-live：需 **加錢** 或 **暫停 Son U**，再跑 checklist 手動 enable live unit。

## 日後 go-live（勿自動執行）

```bash
# 僅供人手 — agent/Desk 唔會自動跑
systemctl enable --now dual-leg-live-v2-newnew-a.service   # 仍須先改 env DRY_RUN=0 等 checklist
journalctl -u dual-leg-live-v2-newnew-a -f
```

Live env：`config/v2_newnew_live_exec.env`（現時 DRY_RUN=1 REAL_MONEY=0）

## Desk API

- `GET /api/v2-newnew/live-control` — prep 狀態
- `POST {"action":"to_live"}` — 只會 **armed prep**，唔 enable live unit
- `POST {"action":"to_paper"}` — 清除 armed prep

## 驗收

- [ ] `high_live_board.rows` 含「V2 newnew A · abort」· 真倉 OFF
- [ ] 紙上 unit active · live unit inactive/disabled
- [ ] Son U exec active · dual-leg-live-v2 inactive
- [ ] `any_live` 僅 Son U（NA 唔亮紅 LIVE ON）

Agent: https://cursor.com/agents/bc-0f2de03a-7841-487a-89fa-a2ec1e7e7a71
""",
        encoding="utf-8",
    )


def main() -> None:
    patch_registry()
    write_live_env()
    write_systemd_unit()
    patch_paper_board()
    patch_app_route()
    write_doc()
    print("patch ok")


if __name__ == "__main__":
    main()
