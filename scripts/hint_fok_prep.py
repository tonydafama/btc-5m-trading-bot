#!/usr/bin/env python3
"""Manual, default-off prep for one BTC 5m FOK buy.

This is not a service. Do not install it as a systemd unit, and do not
start it on a timer. With the default environment it prints the closed
gates and exits without calling Polymarket.

It ignores global DRY_RUN / REAL_MONEY / LIVE_ARMED. A later trial has to
set HINT_FOK_ENABLED=1, HINT_FOK_DRY_RUN=0, HINT_FOK_ARMED=1, and
HINT_FOK_REAL_MONEY=1 in the environment of this process only, after the
user says Go Live in the finance chat.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hint_fok_prep import run_prep  # noqa: E402


def main() -> int:
    result = run_prep()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        text = str(exc)
        redacted = __import__("re").sub(r"0x[a-fA-F0-9]{8,}", "[redacted]", text)
        print(json.dumps({"ok": False, "placed": False, "message": redacted[:240]}, ensure_ascii=False))
        raise SystemExit(1)
