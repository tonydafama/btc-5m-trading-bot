#!/usr/bin/env python3
"""Put the live bot first, and the Live book above the Paper book."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/btc-5m-bot")

OLD_CSS = """.hl-books {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: .45rem;
  margin-top: .4rem;
}"""
NEW_CSS = """.hl-books {
  display: grid;
  grid-template-columns: 1fr;
  gap: .45rem;
  margin-top: .4rem;
}"""

OLD_BOOKS = (
    'aria-label="Paper 同 Live 盈虧同勝率">'
    "${band('paper', 'is-paper')}${band('live', 'is-live')}"
)
NEW_BOOKS = (
    'aria-label="Live 同 Paper 盈虧同勝率">'
    "${band('live', 'is-live')}${band('paper', 'is-paper')}"
)

OLD_SORT = """      const sortRows = [...rows].sort((a, b) => {
        const aid = String(a.id || '');
        const bid = String(b.id || '');
        if (aid === 'son-btc-5m-ultimate-live') return -1;
        if (bid === 'son-btc-5m-ultimate-live') return 1;
        return 0;
      });"""
NEW_SORT = """      const sortRows = [...rows].sort((a, b) => {
        const rank = (r) => (deskBotMode(r) === 'live' && !!r.live_enabled) ? 0 : 1;
        return rank(a) - rank(b);
      });"""


def main() -> None:
    path = ROOT / "dashboard" / "templates" / "v1_only.html"
    text = path.read_text(encoding="utf-8")
    if "grid-template-columns: 1fr 1fr;" in text.split(".hl-books", 1)[-1][:240]:
        if OLD_CSS not in text:
            raise SystemExit("books css missing")
        text = text.replace(OLD_CSS, NEW_CSS, 1)
        print("css updated")
    else:
        print("css already")
    if OLD_BOOKS in text:
        text = text.replace(OLD_BOOKS, NEW_BOOKS, 1)
        print("books updated")
    elif NEW_BOOKS in text:
        print("books already")
    else:
        raise SystemExit("books markup missing")
    if OLD_SORT in text:
        text = text.replace(OLD_SORT, NEW_SORT, 1)
        print("sort updated")
    elif NEW_SORT in text:
        print("sort already")
    else:
        raise SystemExit("sort missing")
    path.write_text(text, encoding="utf-8")
    print("written", path)


if __name__ == "__main__":
    main()
