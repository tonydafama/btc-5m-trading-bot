"""Desk card for 先有波幅，先買中間價.

Reflects bot truth from status files. Desk Paper/Live buttons only switch
which ledger the card displays (desk_view); they never systemctl, never write
DRY_RUN/REAL_MONEY, and never start/stop real money — finance chat owns that.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STRATEGY_ID = "midband-range80"
DISPLAY = "先有波幅，先買中間價"
UNIT_USD = 3.0
DESK_VIEW_NAME = "midband-range80_desk_view.json"
HKT = timezone(timedelta(hours=8))


def _sons(root: Path) -> Path:
    return root / "logs" / "sons"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _session(root: Path) -> dict[str, Any]:
    data = _read_json(_sons(root) / "midband-range80_session.json", {})
    return data if isinstance(data, dict) else {}


def _status(root: Path) -> dict[str, Any]:
    data = _read_json(_sons(root) / "midband-range80_status.json", {})
    return data if isinstance(data, dict) else {}


def _is_live(status: dict[str, Any], session: dict[str, Any]) -> bool:
    if status.get("live_on_air") is True or status.get("real_money") is True:
        return True
    mode = str(status.get("mode") or session.get("mode") or "").lower()
    if mode == "live":
        return True
    if str(status.get("REAL_MONEY") or "") == "1" and str(status.get("DRY_RUN") or "1") == "0":
        return True
    return False


def _desk_view_path(root: Path) -> Path:
    return _sons(root) / DESK_VIEW_NAME


def _desk_view(root: Path, *, exec_live: bool) -> str:
    data = _read_json(_desk_view_path(root), {})
    if isinstance(data, dict):
        v = str(data.get("view") or "").lower()
        if v in ("live", "paper"):
            return v
    return "live" if exec_live else "paper"


def _set_desk_view(root: Path, view: str) -> None:
    view = view if view in ("live", "paper") else "paper"
    path = _desk_view_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "view": view,
        "updated_at_hkt": datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _live_trade_rows(root: Path) -> list[dict[str, Any]]:
    raw = _read_json(_sons(root) / "midband-range80_trades.json", [])
    if not isinstance(raw, list):
        return []
    session = _session(root)
    sid = str(session.get("session_id") or "")
    rows = [r for r in raw if isinstance(r, dict)]
    if sid:
        rows = [r for r in rows if r.get("session_id") == sid]
    return rows


def _paper_archive_paths(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    archive = _sons(root) / "archive"
    if not archive.is_dir():
        return {}, []
    sessions = sorted(archive.glob("midband-range80_session-before-live-*.json"), reverse=True)
    for sp in sessions:
        tag = sp.name.replace("midband-range80_session-before-live-", "").replace(".json", "")
        tp = archive / f"midband-range80_trades-before-live-{tag}.json"
        sess = _read_json(sp, {})
        if not isinstance(sess, dict):
            continue
        raw = _read_json(tp, [])
        rows = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
        sid = str(sess.get("session_id") or "")
        if sid:
            rows = [r for r in rows if r.get("session_id") == sid]
        return sess, rows
    return {}, []


def _ledger_bundle(root: Path, view: str, *, exec_live: bool) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    status = _status(root)
    session = _session(root)
    if view == "live":
        return status, session, _live_trade_rows(root)
    if exec_live:
        paper_sess, paper_rows = _paper_archive_paths(root)
        if paper_sess:
            return status, paper_sess, paper_rows
    return status, session, _live_trade_rows(root)


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    entered = [r for r in rows if r.get("entered")]
    settled = [r for r in entered if r.get("pnl") is not None]
    wins = sum(1 for r in settled if str(r.get("trade_result") or "").upper() == "WIN")
    losses = sum(1 for r in settled if str(r.get("trade_result") or "").upper() == "LOSS")
    pnl = round(sum(float(r.get("pnl") or 0) for r in settled), 2)
    skips = sum(1 for r in rows if not r.get("entered"))
    posted = sum(1 for r in entered if r.get("posted_live_order"))
    return {
        "entered_n": len(entered),
        "settled_n": len(settled),
        "wins": wins,
        "losses": losses,
        "pnl": pnl,
        "skips": skips,
        "posted_live_orders": posted,
    }


def status_payload(root: Path) -> dict[str, Any]:
    status = _status(root)
    session = _session(root)
    exec_live = _is_live(status, session)
    view = _desk_view(root, exec_live=exec_live)
    vstatus, vsession, rows = _ledger_bundle(root, view, exec_live=exec_live)
    st = _stats(rows)
    running = str(status.get("status") or "").startswith("RUNNING")
    capital = vsession.get("capital_usd")
    if capital is None and view == "live":
        capital = status.get("capital_usd") or session.get("capital_usd")
    try:
        capital_n = None if capital is None else round(float(capital), 2)
    except (TypeError, ValueError):
        capital_n = None
    cap_label = vsession.get("capital_label") or status.get("capital_label")
    if capital_n is not None:
        cap_label = cap_label or f"${capital_n:.2f}"
    elif not cap_label:
        cap_label = "待入金"

    exec_status = status.get("status_label") or (
        "跑緊 · 真倉 ON" if exec_live and running else ("跑緊 · Paper / prep · 真倉 OFF" if running else "真倉 OFF")
    )
    view_tc = "檢視 · 真倉 Live" if view == "live" else "檢視 · Paper"
    status_label = f"{exec_status} · {view_tc}"

    if exec_live:
        go_live_tc = "已喺 finance chat 開真倉。Desk 按鈕只切換檢視。"
    else:
        go_live_tc = "未開真倉。要開嘅話喺 finance chat 講 Go Live。"

    sid = str(vsession.get("session_id") or status.get("session_id") or "")

    return {
        "ok": True,
        "id": STRATEGY_ID,
        "display_name": DISPLAY,
        "display_name_en": "Mid-band Range80",
        "exec_mode": "live" if exec_live else "paper",
        "desk_view": view,
        "desk_mode": view,
        "mode": view,
        "live_enabled": exec_live,
        "live_on_air": exec_live,
        "live_armed": exec_live,
        "dry_run": not exec_live,
        "real_money": exec_live,
        "DRY_RUN": "0" if exec_live else "1",
        "REAL_MONEY": "1" if exec_live else "0",
        "TRADING_MODE": "live" if exec_live else "paper",
        "funded": bool(exec_live and capital_n is not None),
        "capital_usd": capital_n,
        "capital_label": cap_label,
        "unit_usd": UNIT_USD,
        "stake_label": "$3/注",
        "pnl": st["pnl"],
        "pnl_label": f"${st['pnl']:.2f}",
        "trade_count": st["entered_n"],
        "settled_n": st["settled_n"],
        "wins": st["wins"],
        "losses": st["losses"],
        "skips": st["skips"],
        "session_id": sid,
        "status": status.get("status") or ("RUNNING · live" if exec_live else "RUNNING · paper"),
        "status_label": status_label,
        "view_label_tc": view_tc,
        "heartbeat_at_hkt": status.get("heartbeat_at_hkt"),
        "rules_tc": (
            "BTC 5 分鐘 Up/Down · 每窗一注 · 單邊 · 無對沖 · 持有到官方結算 · "
            "只睇剩餘 40–75 秒 · 上一根 Binance BTCUSDT 5 分鐘高−低 ≥ $80 · "
            "買第一邊可成交 ask ∈ [0.72, 0.88] · 任一邊 ask > 0.94 跳過該窗 · "
            "兩邊仍在 0.45–0.55 唔買 · 注碼 $3"
        ),
        "go_live_tc": go_live_tc,
        "lines": list(reversed(rows[-40:])),
        "posted_live_orders": st["posted_live_orders"] or int(status.get("posted_live_orders") or 0),
    }


def apply_action(root: Path, action: str) -> dict[str, Any]:
    """Switch desk_view only — never changes the running unit."""
    action = (action or "").strip().lower()
    status = _status(root)
    session = _session(root)
    exec_live = _is_live(status, session)

    if action == "to_paper":
        _set_desk_view(root, "paper")
        payload = status_payload(root)
        payload.update(
            {
                "ok": True,
                "action": action,
                "message_tc": (
                    "已切換檢視 · Paper。"
                    + (" 真倉仍然 ON，要停請喺 finance chat 講。" if exec_live else " 真倉 OFF。")
                ),
            }
        )
        return payload
    if action == "to_live":
        _set_desk_view(root, "live")
        payload = status_payload(root)
        payload.update(
            {
                "ok": True,
                "action": action,
                "message_tc": (
                    "已切換檢視 · 真倉 Live。"
                    if exec_live
                    else "檢視 · 真倉 Live（bot 未開真倉）。開倉請喺 finance chat。"
                ),
            }
        )
        return payload

    payload = status_payload(root)
    payload.update(
        {
            "ok": False,
            "action": action,
            "error": "bad_action",
            "error_tc": "呢張卡只接受 Paper / Live 檢視切換。",
        }
    )
    return payload


def paper_board_row_stats(root: Path) -> dict[str, Any]:
    rows = _live_trade_rows(root)
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
        "session_id": str(_session(root).get("session_id") or ""),
    }
