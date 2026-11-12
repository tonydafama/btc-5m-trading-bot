"""Family Desk card for 先有波幅，先買中間價. Read-only plus a hard live refusal.

Does not systemctl anything, does not write DRY_RUN/REAL_MONEY, and does
not touch Parent V2 or newnew-A ledgers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STRATEGY_ID = "midband-range80"
DISPLAY = "先有波幅，先買中間價"
DEPOSIT_ADDRESS = "0x8081D24BAc2456e5526F9C6eF80CC9e8269A259F"
FUNDER_ADDRESS = "0x2c9c171cf9ba62187e2f4c252929894e47c8f9b3"
UNIT_USD = 3.0

LIVE_REFUSAL_TC = (
    "先有波幅，先買中間價 而家只係 Paper / prep。"
    "未入金，唔會開真倉，亦唔會改 DRY_RUN 或 REAL_MONEY。"
    "入 USDC 去 deposit 0x8081D24BAc2456e5526F9C6eF80CC9e8269A259F 之後，"
    "先同 finance chat 講 Go Live。"
)


def _sons(root: Path) -> Path:
    return root / "logs" / "sons"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _session_id(root: Path) -> str:
    data = _read_json(_sons(root) / "midband-range80_session.json", {})
    if isinstance(data, dict):
        return str(data.get("session_id") or "")
    return ""


def _trades(root: Path) -> list[dict[str, Any]]:
    raw = _read_json(_sons(root) / "midband-range80_trades.json", [])
    if not isinstance(raw, list):
        return []
    sid = _session_id(root)
    rows = [r for r in raw if isinstance(r, dict)]
    if sid:
        tagged = [r for r in rows if r.get("session_id") == sid]
        return tagged
    return rows


def status_payload(root: Path) -> dict[str, Any]:
    status = _read_json(_sons(root) / "midband-range80_status.json", {})
    if not isinstance(status, dict):
        status = {}
    rows = _trades(root)
    entered = [r for r in rows if r.get("entered")]
    settled = [r for r in entered if r.get("pnl") is not None]
    wins = sum(1 for r in settled if str(r.get("trade_result") or "").upper() == "WIN")
    losses = sum(1 for r in settled if str(r.get("trade_result") or "").upper() == "LOSS")
    pnl = round(sum(float(r.get("pnl") or 0) for r in settled), 2)
    skips = sum(1 for r in rows if not r.get("entered"))
    running = str(status.get("status") or "").startswith("RUNNING")
    return {
        "ok": True,
        "id": STRATEGY_ID,
        "display_name": DISPLAY,
        "display_name_en": "Mid-band Range80",
        "mode": "paper",
        "desk_mode": "paper",
        "live_enabled": False,
        "live_on_air": False,
        "live_armed": False,
        "dry_run": True,
        "real_money": False,
        "DRY_RUN": "1",
        "REAL_MONEY": "0",
        "TRADING_MODE": "paper",
        "funded": False,
        "capital_usd": None,
        "capital_label": "待入金",
        "unit_usd": UNIT_USD,
        "stake_label": "$3/注",
        "pnl": pnl,
        "pnl_label": f"${pnl:.2f}",
        "trade_count": len(entered),
        "settled_n": len(settled),
        "wins": wins,
        "losses": losses,
        "skips": skips,
        "session_id": _session_id(root) or status.get("session_id") or "",
        "status": "RUNNING · paper" if running else "READY · paper",
        "status_label": status.get("status_label")
        or ("跑緊 · Paper / prep · 真倉 OFF" if running else "已接線 · Paper / prep · 真倉 OFF · 未有心跳"),
        "heartbeat_at_hkt": status.get("heartbeat_at_hkt"),
        "rules_tc": (
            "BTC 5 分鐘 Up/Down · 每窗一注 · 單邊 · 無對沖 · 持有到官方結算 · "
            "只睇剩餘 40–75 秒 · 上一根 Binance BTCUSDT 5 分鐘高−低 ≥ $80 · "
            "買第一邊可成交 ask ∈ [0.72, 0.88] · 任一邊 ask > 0.94 跳過該窗 · "
            "兩邊仍在 0.45–0.55 唔買 · 注碼 $3"
        ),
        "deposit_address": DEPOSIT_ADDRESS,
        "funder_address": FUNDER_ADDRESS,
        "deposit_note_tc": "入金用 deposit 0x8081…259F。唔好打去 0xB0B1。而家未當有本金。",
        "go_live_tc": LIVE_REFUSAL_TC,
        "lines": list(reversed(rows[-40:])),
        "posted_live_orders": 0,
    }


def apply_action(root: Path, action: str) -> dict[str, Any]:
    """Paper confirm only. Live is refused and no env file is written."""
    action = (action or "").strip().lower()
    payload = status_payload(root)
    if action == "to_paper":
        payload.update(
            {
                "ok": True,
                "action": action,
                "message_tc": "已經係 Paper / prep，真倉 OFF。冇改其他 bot。",
            }
        )
        return payload
    if action == "to_live":
        payload.update(
            {
                "ok": False,
                "action": action,
                "error": "live_refused",
                "error_tc": LIVE_REFUSAL_TC,
                "message_tc": LIVE_REFUSAL_TC,
            }
        )
        return payload
    payload.update(
        {
            "ok": False,
            "action": action,
            "error": "bad_action",
            "error_tc": "呢張卡只接受查詢。Live 未開放。",
        }
    )
    return payload


def paper_board_row_stats(root: Path) -> dict[str, Any]:
    rows = _trades(root)
    entered = [r for r in rows if r.get("entered")]
    settled = [r for r in entered if r.get("pnl") is not None]
    pnl = round(sum(float(r.get("pnl") or 0) for r in settled), 2) if settled else 0.0
    last = ""
    for row in reversed(rows):
        last = str(row.get("logged_at_hkt") or row.get("opened_at_hkt") or "")
        if last:
            break
    return {
        "trade_count": len(entered),
        "scan_count": len(rows),
        "pnl": pnl,
        "settled": len(settled),
        "last_activity": last,
        "session_id": _session_id(root),
    }
