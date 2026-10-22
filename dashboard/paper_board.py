"""Read-only snapshot of bots, paper ledgers, and research.

No trading side effects. Missing logs or the funding-carry tree degrade to
empty rows instead of raising.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

HKT = ZoneInfo("Asia/Hong_Kong")

BOT_UNITS = (
    "btc-5m-observer",
    "eth-5m-observer",
    "crypto-15m-observer",
    "btc-5m-dashboard",
)

_ENV_KEYS = (
    "TRADING_MODE",
    "ETH_TRADING_MODE",
    "CRYPTO_15M_TRADING_MODE",
    "DUAL_LEG_ENABLED",
    "DUAL_LEG_PAPER",
    "ENABLE_DUAL_LEG",
)

_TS_KEYS = (
    "timestamp_hkt",
    "opened_at_hkt",
    "settled_at_hkt",
    "updated_at_hkt",
    "timestamp",
    "ts_utc",
    "ts",
    "updated_at",
    "time",
    "settled_at",
)


def bot_root() -> Path:
    return Path(os.getenv("BTC_5M_ROOT", "/root/btc-5m-bot"))


def funding_root() -> Path:
    return Path(os.getenv("FUNDING_CARRY_ROOT", "/root/funding-carry-bot"))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists() or not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return default


def _read_env_file(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    if not path.exists():
        return found
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return found
    allowed = set(_ENV_KEYS)
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key not in allowed:
            continue
        found[key] = value.strip().strip('"').strip("'")
    return found


def merged_env(root: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    """Process env wins; .env fills gaps. Only mode/flag keys are kept."""
    out = _read_env_file(root / ".env")
    source = env if env is not None else os.environ
    for key in _ENV_KEYS:
        val = source.get(key)
        if val is not None and str(val).strip() != "":
            out[key] = str(val).strip()
    return out


def _tri_flag(value: str | None) -> bool | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text in {"", "none"}:
        return None
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    if text in {"1", "true", "yes", "on", "paper", "enabled"}:
        return True
    return None


def _mode_label(value: str | None, *, default: str = "PAPER") -> str:
    text = (value or default).strip().lower()
    if text == "live":
        return "LIVE"
    return "PAPER"


def _money(value: float | None) -> str | None:
    if value is None:
        return None
    number = round(float(value), 2)
    sign = "+" if number >= 0 else "-"
    return f"{sign}${abs(number):.2f}"


def _as_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("trades", "rows", "log", "signals"):
            val = data.get(key)
            if isinstance(val, list):
                return [row for row in val if isinstance(row, dict)]
    return []


def _row_ts(row: dict[str, Any]) -> str:
    for key in _TS_KEYS:
        val = row.get(key)
        if val:
            return str(val)
    return ""


def _display_ts(value: str) -> str:
    parsed = _coerce_dt(value)
    if parsed is None:
        return _short_ts(value)
    return parsed.astimezone(HKT).strftime("%Y-%m-%d %H:%M HKT")


def _short_ts(value: str) -> str:
    if not value:
        return "—"
    text = value.replace("T", " ").replace("+00:00", " UTC").replace("Z", " UTC")
    if "." in text:
        head, tail = text.split(".", 1)
        if "UTC" in tail:
            text = head + " UTC"
        else:
            text = head
    return text[:22]


def _coerce_dt(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith(" HKT"):
        text = text[: -len(" HKT")].strip()
        naive_hkt = True
    else:
        naive_hkt = "T" not in text and "+" not in text and not text.endswith("Z")
    if naive_hkt:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=HKT)
            except ValueError:
                continue
    return _parse_time(text)


def _latest_ts(rows: list[dict[str, Any]], *extra_paths: Path) -> str:
    best: datetime | None = None
    for row in rows:
        parsed = _coerce_dt(_row_ts(row))
        if parsed is not None and (best is None or parsed > best):
            best = parsed
    if best is None:
        for path in extra_paths:
            if path is None or not path.exists():
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=HKT)
            except OSError:
                continue
            if best is None or mtime > best:
                best = mtime
    if best is None:
        return "—"
    return best.astimezone(HKT).strftime("%Y-%m-%d %H:%M HKT")


def _is_entered(row: dict[str, Any]) -> bool:
    if "entered" in row:
        return bool(row.get("entered"))
    if row.get("trade_result") in {"WIN", "LOSS", "PENDING"}:
        return True
    if row.get("status") in {"settled", "open", "filled"} and row.get("stake") not in (None, 0, "0"):
        return True
    return False


def _is_settled(row: dict[str, Any]) -> bool:
    if not _is_entered(row) and "entered" in row:
        return False
    if row.get("trade_result") in {"WIN", "LOSS"}:
        return True
    if row.get("status") == "settled" and row.get("pnl") is not None:
        return True
    if row.get("won") is not None and row.get("pnl") is not None:
        return True
    return False


def _ledger_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    entered = [row for row in rows if _is_entered(row)]
    settled = [row for row in entered if _is_settled(row)]
    pnl_rows = settled or [row for row in entered if row.get("pnl") is not None]
    pnl = round(sum(float(row.get("pnl") or 0) for row in pnl_rows), 2) if pnl_rows else 0.0
    return {
        "trade_count": len(entered),
        "scan_count": len(rows),
        "pnl": pnl,
        "settled": len(settled),
    }


def _count_label(stats: dict[str, Any]) -> str:
    trades = int(stats.get("trade_count") or 0)
    scans = int(stats.get("scan_count") or 0)
    if trades and scans and scans != trades:
        return f"{trades} 單 · {scans} 窗"
    if trades:
        return f"{trades} 單"
    if scans:
        return f"0 單 · {scans} 次掃描"
    return "0 單"


def _paper_row(
    *,
    row_id: str,
    name: str,
    stats: dict[str, Any] | None,
    last_activity: str,
    note: str,
    pnl: float | None = None,
    trade_count: int | None = None,
    count_label: str | None = None,
    pnl_unknown: bool = False,
) -> dict[str, Any]:
    stats = stats or {"trade_count": 0, "scan_count": 0, "pnl": 0.0}
    if pnl_unknown:
        resolved_pnl = None
    else:
        resolved_pnl = stats["pnl"] if pnl is None else pnl
    resolved_trades = stats["trade_count"] if trade_count is None else trade_count
    return {
        "id": row_id,
        "name": name,
        "mode": "PAPER",
        "real_money": False,
        "trade_count": resolved_trades,
        "scan_count": stats.get("scan_count", 0),
        "count_label": count_label or _count_label({**stats, "trade_count": resolved_trades}),
        "pnl": None if resolved_pnl is None else round(float(resolved_pnl), 2),
        "pnl_label": "—" if resolved_pnl is None else _money(float(resolved_pnl)),
        "last_activity": last_activity or "—",
        "note": note,
    }


def _systemctl_state(unit: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return (result.stdout or "").strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def list_processes() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: list[dict[str, str]] = []
    for line in (result.stdout or "").splitlines():
        text = line.strip()
        if not text:
            continue
        pid, _, cmd = text.partition(" ")
        if not pid.isdigit() or not cmd:
            continue
        cwd = ""
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            cwd = ""
        rows.append({"pid": pid, "cmd": cmd, "cwd": cwd})
    return rows


def _proc_matches(unit: str, proc: dict[str, str], root: Path) -> bool:
    cmd = proc.get("cmd") or ""
    cwd = proc.get("cwd") or ""
    root_s = str(root)
    in_bot = root_s in cmd or root_s in cwd or "btc-5m-bot" in cmd or "btc-5m-bot" in cwd
    if unit in cmd:
        return True
    if unit == "btc-5m-observer":
        if "eth_observer" in cmd or "alpha_max" in cmd:
            return False
        return in_bot and "observer.py" in cmd and "eth_observer.py" not in cmd
    if unit == "eth-5m-observer":
        return "eth_observer.py" in cmd or (in_bot and "eth-5m" in cmd and "observer" in cmd)
    if unit == "crypto-15m-observer":
        return any(
            token in cmd
            for token in ("crypto_15m", "crypto-15m", "15m_observer", "paper_15m_observer")
        )
    if unit == "btc-5m-dashboard":
        return "dashboard/app.py" in cmd or (in_bot and "dashboard" in cmd and "app.py" in cmd)
    return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _funding_pidfiles(root: Path) -> list[Path]:
    return [
        root / "logs" / "funding-carry.pid",
        root / "run" / "funding-carry.pid",
        root / "funding-carry.pid",
    ]


def funding_loop_status(
    root: Path,
    processes: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Detect `main.py --loop` whose cwd or argv sits under funding-carry-bot."""
    procs = list_processes() if processes is None else processes
    root_s = str(root)
    matched: list[str] = []
    for proc in procs:
        cmd = proc.get("cmd") or ""
        cwd = proc.get("cwd") or ""
        if "main.py" not in cmd or "--loop" not in cmd:
            continue
        under = (
            root_s in cmd
            or root_s in cwd
            or "funding-carry-bot" in cmd
            or "funding-carry-bot" in cwd
            or "funding_carry" in cmd
        )
        if under:
            matched.append(proc.get("pid") or "")
    via = "pgrep" if matched else ""
    if not matched:
        for path in _funding_pidfiles(root):
            if not path.exists():
                continue
            try:
                pid = int(path.read_text(encoding="utf-8").strip().split()[0])
            except (OSError, ValueError, IndexError):
                continue
            if _pid_alive(pid):
                matched.append(str(pid))
                via = "pidfile"
                break
    running = bool(matched)
    return {
        "running": running,
        "via": via or "down",
        "pids": matched,
        "label": "Funding PAPER",
        "state": "active" if running else "inactive",
        "active": running,
    }


def funding_carry_service_row(
    root: Path | None = None,
    processes: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    status = funding_loop_status(root or funding_root(), processes)
    return {
        "label": status["label"],
        "state": status["state"],
        "active": status["active"],
        "via": status["via"],
    }


def _service_states(override: dict[str, str] | None) -> dict[str, str]:
    if override is not None:
        return dict(override)
    return {unit: _systemctl_state(unit) for unit in BOT_UNITS}


def _bots(
    *,
    env: dict[str, str],
    states: dict[str, str],
    processes: list[dict[str, str]],
    root: Path,
    funding: Path,
    dashboard_self: bool,
) -> list[dict[str, Any]]:
    specs = (
        (
            "btc-5m-observer",
            "BTC 5m observer",
            _mode_label(env.get("TRADING_MODE")),
            "盯 BTC 每 5 分鐘窗口，行 Package2+ 規則",
        ),
        (
            "eth-5m-observer",
            "ETH 5m observer",
            _mode_label(env.get("ETH_TRADING_MODE")),
            "盯 ETH 每 5 分鐘窗口，行 Package2+ 規則",
        ),
        (
            "crypto-15m-observer",
            "BTC+ETH 15m observer",
            _mode_label(env.get("CRYPTO_15M_TRADING_MODE")),
            "盯 BTC 同 ETH 每 15 分鐘窗口，獨立 session",
        ),
        (
            "btc-5m-dashboard",
            "Dashboard",
            "—",
            "呢個網頁，只顯示狀態，不下單",
        ),
    )
    rows: list[dict[str, Any]] = []
    for unit, name, mode, description in specs:
        state = states.get(unit, "unknown")
        systemd_up = state == "active"
        proc_up = any(_proc_matches(unit, proc, root) for proc in processes)
        self_up = unit == "btc-5m-dashboard" and dashboard_self
        running = systemd_up or proc_up or self_up
        rows.append(
            {
                "id": unit,
                "name": name,
                "status": "running" if running else "stopped",
                "status_label": "RUNNING" if running else "STOPPED",
                "mode": mode,
                "description": description,
                "service_state": state if not self_up else (state or "self"),
            }
        )
    fund = funding_loop_status(funding, processes)
    rows.append(
        {
            "id": "funding-carry-loop",
            "name": "Binance↔KuCoin funding scanner",
            "status": "running" if fund["running"] else "stopped",
            "status_label": "RUNNING" if fund["running"] else "STOPPED",
            "mode": "PAPER",
            "description": "掃 Binance 同 KuCoin 公開 funding，只記紙上信號",
            "service_state": fund["state"],
            "via": fund["via"],
        }
    )
    return rows


def _file_rows(path: Path) -> list[dict[str, Any]]:
    return _as_rows(_read_json(path, []))


def _btc_paper(root: Path, *, live_bot: bool) -> dict[str, Any]:
    path = root / "logs" / "trade_log_paper.json"
    state = root / "logs" / "new_strategy_state.json"
    rows = _file_rows(path)
    stats = _ledger_stats(rows)
    note = "模擬單，唔係真錢"
    if not path.exists():
        note = "未有 trade_log_paper.json · 模擬單，唔係真錢"
    if live_bot:
        note += " · 對應 bot 而家 LIVE"
    return _paper_row(
        row_id="btc-5m-package2-paper",
        name="BTC 5m Package2+",
        stats=stats,
        last_activity=_latest_ts(rows, state if state.exists() else path),
        note=note,
    )


def _eth_paper(root: Path, *, live_bot: bool) -> dict[str, Any]:
    path = root / "logs" / "trade_log_eth_paper.json"
    rows = _file_rows(path)
    stats = _ledger_stats(rows)
    note = "模擬單，唔係真錢"
    if not path.exists():
        note = "未有 trade_log_eth_paper.json · 模擬單，唔係真錢"
    if live_bot:
        note += " · 對應 bot 而家 LIVE"
    return _paper_row(
        row_id="eth-5m-package2-paper",
        name="ETH 5m Package2+",
        stats=stats,
        last_activity=_latest_ts(rows, path),
        note=note,
    )


def _import_summary(module: str, fn_name: str) -> dict[str, Any] | None:
    try:
        mod = __import__(module, fromlist=[fn_name])
    except Exception:
        return None
    try:
        data = getattr(mod, fn_name)()
    except Exception:
        return {"_unavailable": True}
    if isinstance(data, dict):
        return data
    return {"_unavailable": True}


def _resolve_summary(
    loaders: dict[str, Any] | None,
    key: str,
    module: str,
    fn_name: str,
) -> dict[str, Any] | None:
    if loaders is not None and key in loaders:
        val = loaders[key]
        if val is None:
            return None
        if callable(val):
            try:
                called = val()
            except Exception:
                return {"_unavailable": True}
            return called if isinstance(called, dict) else {"_unavailable": True}
        return val if isinstance(val, dict) else {"_unavailable": True}
    return _import_summary(module, fn_name)


def _named_logs(root: Path, pred: Callable[[str], bool]) -> list[Path]:
    logs = root / "logs"
    if not logs.exists():
        return []
    found: list[Path] = []
    try:
        paths = logs.rglob("*.json")
    except OSError:
        return []
    for path in paths:
        try:
            if path.is_file() and pred(path.name.lower()):
                found.append(path)
        except OSError:
            continue
    return sorted(found, key=lambda item: item.stat().st_mtime, reverse=True)


def _summary_pnl(summary: dict[str, Any]) -> tuple[float | None, int | None]:
    btc = summary.get("btc") if isinstance(summary.get("btc"), dict) else {}
    eth = summary.get("eth") if isinstance(summary.get("eth"), dict) else {}
    trades = None
    if "trades_entered" in btc or "trades_entered" in eth:
        trades = int(btc.get("trades_entered") or 0) + int(eth.get("trades_entered") or 0)
    elif summary.get("trades_entered") is not None:
        trades = int(summary.get("trades_entered") or 0)
    elif summary.get("closed") is not None:
        trades = int(summary.get("closed") or 0)
    pnl = summary.get("combined_overall_pnl")
    if pnl is None:
        pnl = summary.get("pnl")
    if pnl is None:
        pnl = summary.get("overall_pnl")
    if pnl is None and btc and eth and (btc.get("overall_pnl") is not None or eth.get("overall_pnl") is not None):
        pnl = float(btc.get("overall_pnl") or 0) + float(eth.get("overall_pnl") or 0)
    return (None if pnl is None else round(float(pnl), 2), trades)


def _crypto15m_paper(root: Path, summary: dict[str, Any] | None) -> dict[str, Any]:
    mode = "paper"
    if isinstance(summary, dict) and not summary.get("_unavailable"):
        mode = str(summary.get("mode") or "paper").lower()
    disk = _named_logs(
        root,
        lambda name: ("15m" in name or "crypto15m" in name or "crypto_15m" in name)
        and ("trade" in name or "paper" in name or "log" in name),
    )
    disk_rows: list[dict[str, Any]] = []
    disk_path = disk[0] if disk else None
    if disk_path is not None:
        disk_rows = _file_rows(disk_path)
    disk_stats = _ledger_stats(disk_rows) if disk_rows else None

    if mode == "live":
        paper_pnl = None
        if isinstance(summary, dict):
            for key in ("paper_pnl", "paper_overall_pnl", "combined_paper_pnl"):
                if summary.get(key) is not None:
                    paper_pnl = round(float(summary[key]), 2)
                    break
        if paper_pnl is None and disk_stats is not None:
            paper_pnl = disk_stats["pnl"]
        trades = disk_stats["trade_count"] if disk_stats else None
        note = "模擬單，唔係真錢 · 15m bot 而家 LIVE，呢行只用紙上數字"
        if paper_pnl is None and trades is None:
            note = "15m bot 而家 LIVE，紙上數字未分開 · 模擬帳先會出現喺呢度"
        last = _latest_ts(disk_rows, disk_path) if disk_path else "—"
        stats = disk_stats or {"trade_count": 0, "scan_count": 0, "pnl": 0.0}
        return _paper_row(
            row_id="crypto-15m-paper",
            name="BTC+ETH 15m",
            stats=stats,
            last_activity=last,
            note=note,
            pnl=paper_pnl,
            trade_count=0 if trades is None else trades,
            count_label="—" if trades is None else _count_label({**stats, "trade_count": trades}),
            pnl_unknown=paper_pnl is None,
        )

    pnl, trades = (None, None)
    if isinstance(summary, dict) and not summary.get("_unavailable"):
        pnl, trades = _summary_pnl(summary)
    if pnl is None and disk_stats is not None:
        pnl = disk_stats["pnl"]
    if trades is None and disk_stats is not None:
        trades = disk_stats["trade_count"]
    stats = disk_stats or {"trade_count": trades or 0, "scan_count": trades or 0, "pnl": pnl or 0.0}
    last = "—"
    if isinstance(summary, dict):
        sess = summary.get("session") if isinstance(summary.get("session"), dict) else {}
        last = _display_ts(str(sess.get("updated_at_hkt") or sess.get("started_at_hkt") or ""))
    if last == "—" and disk_path is not None:
        last = _latest_ts(disk_rows, disk_path)
    note = "模擬單，唔係真錢"
    if summary is None and not disk_rows:
        note = "未讀到 15m summary · 模擬單，唔係真錢"
    elif isinstance(summary, dict) and summary.get("_unavailable"):
        note = "15m summary 暫時讀唔到 · 模擬單，唔係真錢"
    return _paper_row(
        row_id="crypto-15m-paper",
        name="BTC+ETH 15m",
        stats=stats,
        last_activity=last,
        note=note,
        pnl=0.0 if pnl is None else pnl,
        trade_count=0 if trades is None else trades,
    )


def _dual_leg_enabled(env: dict[str, str], summary: dict[str, Any] | None, root: Path) -> bool:
    flags = [
        _tri_flag(env.get(key))
        for key in ("DUAL_LEG_ENABLED", "DUAL_LEG_PAPER", "ENABLE_DUAL_LEG")
    ]
    if any(flag is True for flag in flags):
        return True
    if any(flag is False for flag in flags):
        return False
    if isinstance(summary, dict) and summary.get("_unavailable"):
        return True
    if isinstance(summary, dict) and not summary.get("_unavailable"):
        if _tri_flag(str(summary.get("enabled"))) is False:
            return False
        return True
    logs = _named_logs(root, lambda name: "dual" in name and "leg" in name)
    return bool(logs)


def _dual_leg_paper(root: Path, summary: dict[str, Any] | None) -> dict[str, Any]:
    logs = _named_logs(root, lambda name: "dual" in name and "leg" in name)
    rows: list[dict[str, Any]] = []
    path = logs[0] if logs else None
    if path is not None:
        rows = _file_rows(path)
    stats = _ledger_stats(rows) if rows else {"trade_count": 0, "scan_count": 0, "pnl": 0.0}
    pnl = stats["pnl"]
    trades = stats["trade_count"]
    if isinstance(summary, dict) and not summary.get("_unavailable"):
        if summary.get("pnl") is not None:
            pnl = round(float(summary["pnl"]), 2)
        if summary.get("closed") is not None:
            trades = int(summary.get("closed") or 0)
        elif summary.get("trades") is not None:
            trades = int(summary.get("trades") or 0)
    last = _latest_ts(rows, path) if path else "—"
    note = "模擬單，唔係真錢 · 雙腿對沖紙上帳"
    if isinstance(summary, dict) and summary.get("_unavailable"):
        note = "dual-leg summary 暫時讀唔到 · 模擬單，唔係真錢"
    return _paper_row(
        row_id="dual-leg-paper",
        name="Dual-leg（BTC/ETH）",
        stats={**stats, "trade_count": trades, "pnl": pnl},
        last_activity=last,
        note=note,
        pnl=pnl,
        trade_count=trades,
    )


def _age_label(seconds: float | None) -> str:
    if seconds is None:
        return "未有掃描"
    if seconds < 90:
        return "剛剛"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} 分鐘前"
    hours = int(minutes // 60)
    if hours < 48:
        return f"{hours} 小時前"
    return f"{int(hours // 24)} 日前"


def _parse_time(value: str) -> datetime | None:
    text = value.strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _latest_scan(root: Path) -> tuple[Path | None, dict[str, Any] | None]:
    scan_dir = root / "logs" / "scans"
    if not scan_dir.exists():
        return None, None
    files = sorted(
        [path for path in scan_dir.glob("*.json") if path.is_file()],
        key=lambda item: item.stat().st_mtime,
    )
    if not files:
        return None, None
    path = files[-1]
    data = _read_json(path, None)
    return path, data if isinstance(data, dict) else None


def _paper_signal_rows(root: Path) -> list[dict[str, Any]]:
    paper_dir = root / "logs" / "paper"
    if not paper_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    files: list[Path] = []
    try:
        files = [path for path in paper_dir.rglob("*") if path.is_file() and path.suffix in {".jsonl", ".json"}]
    except OSError:
        return []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if path.suffix == ".jsonl":
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
        else:
            rows.extend(_as_rows(_read_json(path, [])))
    return rows


def _signal_pnl(rows: list[dict[str, Any]]) -> tuple[float, bool]:
    total = 0.0
    found = False
    for row in rows:
        hypo = row.get("hypothetical") if isinstance(row.get("hypothetical"), dict) else {}
        for source in (row, hypo):
            for key in ("pnl", "paper_pnl", "realized_pnl"):
                if source.get(key) is not None:
                    total += float(source[key])
                    found = True
                    break
            else:
                continue
            break
    return (round(total, 2), found)


def _funding_snapshot(scan: dict[str, Any] | None) -> str:
    if not scan:
        return "未有 funding 快照"
    venues = scan.get("venues") if isinstance(scan.get("venues"), dict) else {}
    bits: list[str] = []
    for venue, assets in venues.items():
        if not isinstance(assets, dict):
            continue
        for asset, row in assets.items():
            if not isinstance(row, dict) or row.get("funding_rate") is None:
                continue
            try:
                rate = float(row["funding_rate"])
            except (TypeError, ValueError):
                continue
            bits.append(f"{venue} {asset} {rate:+.3%}")
            if len(bits) >= 4:
                return " · ".join(bits)
    return " · ".join(bits) if bits else "未有 funding 快照"


def _best_apr(scan: dict[str, Any] | None) -> str | None:
    if not scan:
        return None
    ideas = scan.get("ideas") if isinstance(scan.get("ideas"), list) else []
    values: list[float] = []
    for idea in ideas:
        if isinstance(idea, dict) and idea.get("net_apr") is not None:
            try:
                values.append(float(idea["net_apr"]))
            except (TypeError, ValueError):
                continue
    if not values:
        return None
    return f"最高 net APR {max(values):+.2%}"


def _flag_count(scan: dict[str, Any] | None, signals: list[dict[str, Any]]) -> int:
    count = 0
    ideas = (scan or {}).get("ideas") if isinstance((scan or {}).get("ideas"), list) else []
    for idea in ideas:
        if isinstance(idea, dict) and idea.get("flag"):
            count += 1
    for row in signals:
        idea = row.get("idea") if isinstance(row.get("idea"), dict) else {}
        if row.get("flag") or idea.get("flag"):
            count += 1
    return count


def _funding_experiment(root: Path, *, scanner_running: bool) -> dict[str, Any]:
    exists = root.exists()
    scan_path, scan = _latest_scan(root) if exists else (None, None)
    signals = _paper_signal_rows(root) if exists else []
    flags = _flag_count(scan, signals)
    pnl, realized = _signal_pnl(signals)
    age = None
    if scan and scan.get("ts_utc"):
        parsed = _parse_time(str(scan["ts_utc"]))
        if parsed is not None:
            age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    if age is None and scan_path is not None:
        try:
            age = datetime.now(timezone.utc).timestamp() - scan_path.stat().st_mtime
        except OSError:
            age = None
    scan_count = 0
    scan_dir = root / "logs" / "scans"
    if scan_dir.exists():
        scan_count = len([path for path in scan_dir.glob("*.json") if path.is_file()])
    apr = _best_apr(scan)
    snapshot = _funding_snapshot(scan)
    if not exists:
        summary = "scanner 目錄唔喺度 · 未有掃描 · 紙上盈虧 $0"
    elif flags == 0:
        summary = f"未有 FLAG · 掃描 {scan_count} 次 · 最新 {_age_label(age)} · 紙上盈虧 {_money(pnl)}"
    else:
        summary = (
            f"FLAG {flags} · 紙上信號 {len(signals)} · 掃描 {scan_count} 次 · "
            f"最新 {_age_label(age)} · 紙上盈虧 {_money(pnl)}"
        )
    if not realized and exists:
        summary += " · 未有已實現盈虧"
    detail_bits = [snapshot]
    if apr:
        detail_bits.append(apr)
    if scanner_running:
        detail_bits.append("scanner 進程見第 A 區")
    return {
        "id": "funding-research",
        "name": "Funding / carry 研究",
        "kind": "research",
        "is_bot": False,
        "status_label": "研究 only",
        "summary": summary,
        "detail": " · ".join(detail_bits),
        "flag_count": flags,
        "signal_count": len(signals),
        "scan_count": scan_count,
        "paper_pnl": pnl,
        "pnl_label": _money(pnl),
        "latest_scan_age": _age_label(age),
        "snapshot": snapshot,
    }


def _metric_dicts(obj: Any, hint: str = "") -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(obj, dict):
        keys = {str(key).lower() for key in obj}
        has_n = bool(keys & {"n", "oos_n", "trades", "samples", "count", "windows"})
        has_stat = bool(keys & {"wr", "win_rate", "win_rate_pct", "winrate", "net", "net_pnl", "total_pnl", "pnl"})
        if has_n and has_stat:
            found.append((hint.lower(), obj))
        for key, value in obj.items():
            found.extend(_metric_dicts(value, str(key)))
    elif isinstance(obj, list):
        for item in obj[:40]:
            found.extend(_metric_dicts(item, hint))
    return found


def _pick_number(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if lowered.get(key) is None:
            continue
        try:
            return float(lowered[key])
        except (TypeError, ValueError):
            continue
    return None


def _fmt_wr(value: float) -> str:
    pct = value * 100 if abs(value) <= 1 else value
    rounded = round(pct)
    if abs(pct - rounded) < 0.05:
        return f"{rounded:.0f}%"
    return f"{pct:.1f}%"


def _fair_value_files(root: Path) -> list[Path]:
    found: list[Path] = []
    bases = [root / "logs" / "backtests", root / "logs"]
    for base in bases:
        if not base.exists():
            continue
        try:
            paths = list(base.rglob("*.json"))
        except OSError:
            continue
        for path in paths:
            name = path.name.lower()
            if "fair_value" in name or "fair-value" in name or "fairvalue" in name:
                found.append(path)
            elif base.name == "backtests" and "calibration" in name:
                found.append(path)
    unique = {path.resolve(): path for path in found if path.is_file()}
    return sorted(unique.values(), key=lambda item: item.stat().st_mtime, reverse=True)


def _fair_value_script(root: Path) -> bool:
    bases = [root / "scripts", root]
    for base in bases:
        if not base.exists():
            continue
        try:
            paths = list(base.glob("*.py"))
        except OSError:
            continue
        for path in paths:
            name = path.name.lower()
            if "fair_value" in name or "fair-value" in name or "fairvalue" in name:
                return True
    return False


def _fair_value_experiment(root: Path) -> dict[str, Any]:
    files = _fair_value_files(root)
    script = _fair_value_script(root)
    chosen: dict[str, Any] | None = None
    chosen_hint = ""
    chosen_name = ""
    for path in files:
        data = _read_json(path, None)
        metrics = _metric_dicts(data)
        if not metrics:
            continue
        preferred = [item for item in metrics if any(token in item[0] for token in ("oos", "out_of_sample", "out-of-sample", "holdout"))]
        hint, row = (preferred or metrics)[-1]
        chosen = row
        chosen_hint = hint
        chosen_name = path.name
        break
    if chosen is None:
        summary = "腳本有，未當 bot 跑"
        if not script and not files:
            summary = "腳本有，未當 bot 跑"
        elif files and not script:
            summary = "有結果檔但未見到 OOS 數字 · 腳本有，未當 bot 跑"
        return {
            "id": "fair-value",
            "name": "Fair-value 研究",
            "kind": "research",
            "is_bot": False,
            "status_label": "研究 only",
            "summary": summary,
            "detail": "唔係下單 bot",
            "oos": None,
        }
    n_val = _pick_number(chosen, ("n", "oos_n", "trades", "samples", "count", "windows"))
    wr_val = _pick_number(chosen, ("wr", "win_rate", "win_rate_pct", "winrate"))
    net_val = _pick_number(chosen, ("net", "net_pnl", "total_pnl", "pnl"))
    bits = []
    if n_val is not None:
        bits.append(f"OOS n={int(n_val)}")
    if wr_val is not None:
        bits.append(f"勝率 {_fmt_wr(wr_val)}")
    if net_val is not None:
        bits.append(f"net {_money(net_val)}")
    summary = " · ".join(bits) if bits else "腳本有，未當 bot 跑"
    summary += " · 研究 only，未當 bot 跑"
    return {
        "id": "fair-value",
        "name": "Fair-value 研究",
        "kind": "research",
        "is_bot": False,
        "status_label": "研究 only",
        "summary": summary,
        "detail": f"結果檔 {chosen_name}" + (f" · {chosen_hint}" if chosen_hint else ""),
        "oos": {
            "n": None if n_val is None else int(n_val),
            "win_rate": wr_val,
            "net": None if net_val is None else round(net_val, 2),
            "file": chosen_name,
        },
    }


def build_all_paper_strategies(
    *,
    bot_root_path: Path | None = None,
    funding_root_path: Path | None = None,
    env: dict[str, str] | None = None,
    service_states: dict[str, str] | None = None,
    processes: list[dict[str, str]] | None = None,
    loaders: dict[str, Any] | None = None,
    dashboard_self: bool = True,
) -> dict[str, Any]:
    """Return `{bots, paper, experiments}` for the dashboard panel."""
    root = bot_root_path or bot_root()
    funding = funding_root_path or funding_root()
    modes = merged_env(root, env)
    states = _service_states(service_states)
    procs = list_processes() if processes is None else processes
    bots = _bots(
        env=modes,
        states=states,
        processes=procs,
        root=root,
        funding=funding,
        dashboard_self=dashboard_self,
    )
    btc_live = _mode_label(modes.get("TRADING_MODE")) == "LIVE"
    eth_live = _mode_label(modes.get("ETH_TRADING_MODE")) == "LIVE"
    paper = [
        _btc_paper(root, live_bot=btc_live),
        _eth_paper(root, live_bot=eth_live),
    ]
    crypto_summary = _resolve_summary(loaders, "crypto15m", "paper_15m_engine", "crypto_15m_summary")
    paper.append(_crypto15m_paper(root, crypto_summary))
    dual_summary = _resolve_summary(loaders, "dual_leg", "dual_leg_paper", "summary")
    if _dual_leg_enabled(modes, dual_summary, root):
        paper.append(_dual_leg_paper(root, dual_summary))

    scanner_running = any(row["id"] == "funding-carry-loop" and row["status"] == "running" for row in bots)
    experiments = [
        _fair_value_experiment(root),
        _funding_experiment(funding, scanner_running=scanner_running),
    ]
    running = sum(1 for row in bots if row["status"] == "running")
    any_live = any(row.get("mode") == "LIVE" for row in bots)
    title = "全部 Paper · 冇 live 真下單" if not any_live else "有 LIVE 模式 · Paper 區只顯示模擬帳"
    line = f"{running} 個 bot 跑緊 / {len(paper)} 條 paper 策略 / {len(experiments)} 項實驗"
    return {
        "ok": True,
        "hero": {
            "title": title,
            "any_live": any_live,
            "bots_running": running,
            "bots_total": len(bots),
            "paper_count": len(paper),
            "experiment_count": len(experiments),
            "line": line,
        },
        "bots": bots,
        "paper": paper,
        "experiments": experiments,
        "debug": {
            "bot_root": str(root),
            "funding_root": str(funding),
            "funding_root_exists": funding.exists(),
            "btc_paper_log": (root / "logs" / "trade_log_paper.json").exists(),
            "eth_paper_log": (root / "logs" / "trade_log_eth_paper.json").exists(),
        },
    }
