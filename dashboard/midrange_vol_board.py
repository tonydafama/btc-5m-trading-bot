"""Dashboard helpers for midrange-vol strategy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from midrange_vol_engine import dashboard_snapshot, events_path


def tail_events(limit: int = 80) -> list[dict[str, Any]]:
    path = events_path()
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
