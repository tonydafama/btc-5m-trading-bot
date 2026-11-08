#!/usr/bin/env python3
"""Keep a refused Live/Paper result on the card after the dashboard refresh."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/btc-5m-bot")

OLD_HINT = ".desk-bot-hint { min-height:1rem; margin:.35rem 0 0; font-size:.7rem; color:var(--amber); }"
NEW_HINT = """.desk-bot-hint { min-height:1rem; margin:.35rem 0 0; font-size:.7rem; color:var(--amber); }
.desk-bot-hint.is-error { color:#ff8d8d; font-weight:750; }"""

OLD_POST = """      const msg = data.message_tc || data.error_tc || data.error || (data.ok ? '已更新' : '失敗');
      if (hint) hint.textContent = msg;
      const noteEl = $('hl-note');
      if (noteEl && msg) noteEl.textContent = msg + ' ｜ ' + (noteEl.textContent || '').split(' ｜ ').slice(-2).join(' ｜ ');
      if (data.ok && (action === 'to_live' || action === 'to_paper') && card) {
        paintDeskCard(card, data.desk_mode || data.mode || 'paper');
      }
      await refresh();"""

NEW_POST = """      const msg = data.message_tc || data.error_tc || data.error || (data.ok ? '已更新' : '失敗');
      _deskFlash = { botId: botId, msg: msg, ok: !!data.ok };
      if (hint) {
        hint.textContent = msg;
        hint.classList.toggle('is-error', !data.ok);
      }
      if (data.ok && (action === 'to_live' || action === 'to_paper') && card) {
        paintDeskCard(card, data.desk_mode || data.mode || 'paper');
      }
      await refresh();
      applyDeskFlash();"""

FLASH_FN = """
  let _deskFlash = null;
  function applyDeskFlash() {
    if (!_deskFlash || !_deskFlash.msg) return;
    const card = document.querySelector('[data-desk-bot-id="' + _deskFlash.botId + '"]');
    const hint = card && card.querySelector('[data-bot-hint]');
    if (hint) {
      hint.textContent = _deskFlash.msg;
      hint.classList.toggle('is-error', !_deskFlash.ok);
    }
    const noteEl = $('hl-note');
    if (noteEl && !_deskFlash.ok) noteEl.textContent = _deskFlash.msg;
  }

"""

NOTE_OLD = """    if (noteEl) noteEl.textContent = bits.join(' ｜ ');
    wireDeskCards(rows);"""
NOTE_NEW = """    if (noteEl) noteEl.textContent = bits.join(' ｜ ');
    wireDeskCards(rows);
    applyDeskFlash();"""


def main() -> None:
    path = ROOT / "dashboard" / "templates" / "v1_only.html"
    text = path.read_text(encoding="utf-8")
    if "function applyDeskFlash" not in text:
        anchor = "  async function postDeskBot(botId, action, betSize) {"
        if anchor not in text:
            raise SystemExit("postDeskBot missing")
        text = text.replace(anchor, FLASH_FN + anchor, 1)
        print("flash fn added")
    else:
        print("flash fn already")
    if OLD_POST in text:
        text = text.replace(OLD_POST, NEW_POST, 1)
        print("post updated")
    elif "applyDeskFlash();" in text and "_deskFlash = { botId" in text:
        print("post already")
    else:
        raise SystemExit("postDeskBot body missing")
    if NOTE_OLD in text:
        text = text.replace(NOTE_OLD, NOTE_NEW, 1)
        print("render reapplies flash")
    elif "wireDeskCards(rows);\n    applyDeskFlash();" in text:
        print("render already")
    else:
        raise SystemExit("renderHighLive note missing")
    if OLD_HINT in text:
        text = text.replace(OLD_HINT, NEW_HINT, 1)
        print("hint css")
    elif ".desk-bot-hint.is-error" in text:
        print("hint css already")
    else:
        raise SystemExit("hint css missing")
    path.write_text(text, encoding="utf-8")
    print("written", path)


if __name__ == "__main__":
    main()
