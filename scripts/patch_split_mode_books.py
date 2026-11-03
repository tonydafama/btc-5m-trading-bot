#!/usr/bin/env python3
"""Show Paper and Live PnL / win rate separately on the three gold-box cards.

Patches the droplet Family Desk files in place. Safe to run again.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/btc-5m-bot")

CSS = """
.hl-books {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: .45rem;
  margin-top: .4rem;
}
.hl-book {
  border-radius: 8px;
  padding: .45rem .55rem .42rem;
  background: #0d1522;
  border: 1px solid #1c2b40;
}
.hl-book.is-paper { border-color: rgba(62,207,142,.38); }
.hl-book.is-live { border-color: rgba(240,113,120,.42); }
.hl-book-k { font-size: .68rem; font-weight: 800; letter-spacing: .02em; }
.hl-book.is-paper .hl-book-k { color: #3ecf8e; }
.hl-book.is-live .hl-book-k { color: #ff8d8d; }
.hl-book-line {
  display: grid;
  grid-template-columns: 2.2rem minmax(2.6rem, .8fr) minmax(4.2rem, 1fr) minmax(4.6rem, 1.15fr);
  gap: .25rem;
  align-items: baseline;
  margin-top: .28rem;
  font-weight: 750;
  font-size: .82rem;
}
.hl-book-line .scope { color: var(--dim); font-size: .66rem; font-weight: 700; }
.hl-book-line .wr { color: #f0d9a8; }
.hl-book-line .pos { color: #3ecf8e; }
.hl-book-line .neg { color: #f07178; }
.hl-book-line .mut { color: #9aa7b6; }
.hl-book-when {
  margin-top: .16rem;
  font-family: var(--mono);
  font-size: .68rem;
  font-weight: 700;
  color: #d5deea;
  line-height: 1.35;
}
.hl-book-rowwhen {
  margin-top: .22rem;
  font-family: var(--mono);
  font-size: .62rem;
  color: #9eb0c4;
}
.hl-book-note { margin-top: .12rem; color: #8fa3ba; font-size: .62rem; line-height: 1.35; }
.hl-stats-mini { grid-template-columns: repeat(2, minmax(96px, 1fr)); }
@media (max-width: 720px) {
  .hl-books { grid-template-columns: 1fr; }
}
"""

JS = r"""
  function modeBooksHtml(books, stake, cap) {
    const band = (key, klass) => {
      const block = (books && books[key]) || {};
      const rows = Array.isArray(block.rows) ? block.rows : [];
      const stamp = [block.when_label, block.duration_label].filter(Boolean).join(' · ');
      const lines = rows.map((b) => {
        const pnlCls = pnlClass(b.pnl);
        const rowStamp = (rows.length > 1 && b.when_label)
          ? `<div class="hl-book-rowwhen">${esc([b.when_label, b.duration_label].filter(Boolean).join(' · '))}</div>`
          : '';
        return `${rowStamp}<div class="hl-book-line">
            <span class="scope">${esc(b.scope || '')}</span>
            <span class="wr">${esc(String(b.wr_label || '未有結算'))}</span>
            <span class="${pnlCls}">${esc(String(b.pnl_label || money(b.pnl)))}</span>
            <span>${esc(String(b.wl_label || '0W/0L'))}</span>
          </div>
          <div class="hl-book-note">${esc(String(b.note || ''))}</div>`;
      }).join('');
      return `<div class="hl-book ${klass}">
          <div class="hl-book-k">${esc(block.title || key)}</div>
          ${stamp ? `<div class="hl-book-when">${esc(stamp)}</div>` : ''}
          ${lines}
        </div>`;
    };
    return `<div class="hl-books" aria-label="Paper 同 Live 盈虧同勝率">${band('paper', 'is-paper')}${band('live', 'is-live')}</div>
          <div class="hl-stats hl-stats-mini">
            <div class="hl-stat"><div class="k">注碼</div><div class="v">${esc(stake)}</div></div>
            <div class="hl-stat"><div class="k">本金</div><div class="v">${esc(cap)}</div></div>
          </div>`;
  }

"""

BOARD_ANCHOR = """    any_live = _apply_sonu_on_air_high_live(root, rows_out, wallet)
    if any_live:
        blocked = None
    return {"""

BOARD_INSERT = """    any_live = _apply_sonu_on_air_high_live(root, rows_out, wallet)
    if any_live:
        blocked = None
    try:
        try:
            from mode_books import attach_mode_books
        except ImportError:
            from dashboard.mode_books import attach_mode_books
        attach_mode_books(root, rows_out)
    except Exception:
        pass
    return {"""

STATS_OLD = """          <div class="hl-stats">
            <div class="hl-stat"><div class="k">WR</div><div class="v wr">${esc(String(wr))}</div></div>
            <div class="hl-stat"><div class="k">PnL</div><div class="v ${pnlCls}">${esc(pnl)}</div></div>
            <div class="hl-stat"><div class="k">結算</div><div class="v">${esc(settled)}</div></div>
            <div class="hl-stat"><div class="k">單數</div><div class="v">${esc(String(r.count_label || ((r.trade_count != null ? r.trade_count : 0) + ' 單')))}</div></div>
            <div class="hl-stat"><div class="k">注碼</div><div class="v">${esc(stake)}</div></div>
            <div class="hl-stat"><div class="k">本金</div><div class="v">${esc(cap)}</div></div>
          </div>"""

STATS_NEW = """          ${(r.mode_books && r.mode_books.paper && r.mode_books.live) ? modeBooksHtml(r.mode_books, stake, cap) : `<div class="hl-stats">
            <div class="hl-stat"><div class="k">WR</div><div class="v wr">${esc(String(wr))}</div></div>
            <div class="hl-stat"><div class="k">PnL</div><div class="v ${pnlCls}">${esc(pnl)}</div></div>
            <div class="hl-stat"><div class="k">結算</div><div class="v">${esc(settled)}</div></div>
            <div class="hl-stat"><div class="k">單數</div><div class="v">${esc(String(r.count_label || ((r.trade_count != null ? r.trade_count : 0) + ' 單')))}</div></div>
            <div class="hl-stat"><div class="k">注碼</div><div class="v">${esc(stake)}</div></div>
            <div class="hl-stat"><div class="k">本金</div><div class="v">${esc(cap)}</div></div>
          </div>`}"""


def patch_board(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "attach_mode_books" in text:
        print(f"board already patched: {path}")
        return
    if BOARD_ANCHOR not in text:
        raise SystemExit(f"paper_board anchor missing: {path}")
    path.write_text(text.replace(BOARD_ANCHOR, BOARD_INSERT, 1), encoding="utf-8")
    print(f"patched board: {path}")


def patch_template(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    changed = False
    if ".hl-books {" not in text:
        needle = ".hl-stat .v.neg { color:#f07178; }\n"
        if needle not in text:
            raise SystemExit(f"css anchor missing: {path}")
        text = text.replace(needle, needle + CSS, 1)
        changed = True
    if "function modeBooksHtml" not in text:
        needle = "  function renderHighLive(board) {"
        if needle not in text:
            raise SystemExit(f"js anchor missing: {path}")
        text = text.replace(needle, JS + needle, 1)
        changed = True
    if "modeBooksHtml(r.mode_books, stake, cap)" not in text:
        if STATS_OLD not in text:
            raise SystemExit(f"stats block missing: {path}")
        text = text.replace(STATS_OLD, STATS_NEW, 1)
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
        print(f"patched template: {path}")
    else:
        print(f"template already patched: {path}")


def main() -> None:
    patch_board(ROOT / "dashboard" / "paper_board.py")
    patch_template(ROOT / "dashboard" / "templates" / "v1_only.html")


if __name__ == "__main__":
    main()
