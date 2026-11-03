#!/usr/bin/env python3
"""Align Live gold box: Son U + parent V2 + newnew A, same card chrome."""
from pathlib import Path

ROOT = Path("/root/btc-5m-bot")


def patch_board() -> None:
    path = ROOT / "dashboard" / "paper_board.py"
    text = path.read_text(encoding="utf-8")
    old = 'if not any(str(r.get("id") or "") in ("dual-leg-live-v2", "son-btc-2legs-v2-newnew-a") for r in rows_out):'
    new = 'if not any(str(r.get("id") or "") == "dual-leg-live-v2" for r in rows_out):'
    if old not in text and new not in text:
        raise SystemExit("paper_board v2 row guard missing")
    text = text.replace(old, new)
    text = text.replace(
        '"display_name": "BTC 2 legs V2 · 曾live"',
        '"display_name": "BTC 2 legs V2"',
    )
    path.write_text(text, encoding="utf-8")


def patch_template() -> None:
    path = ROOT / "dashboard" / "templates" / "v1_only.html"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "const DESK_BOT_IDS = ['son-btc-5m-ultimate-live', 'dual-leg-live-v2'];",
        "const DESK_BOT_IDS = ['son-btc-5m-ultimate-live', 'dual-leg-live-v2', 'son-btc-2legs-v2-newnew-a'];",
    )
    old_meta = """    if (id === 'dual-leg-live-v2') return { mult: 2, rule: 'v2', name: 'BTC 2 legs V2', cls: 'desk-v2' };
    if (id === 'son-btc-5m-ultimate-live') return { mult: 1, rule: 'v2 5m btc', name: '終極5m', cls: 'desk-5m' };
    return null;"""
    new_meta = """    if (id === 'dual-leg-live-v2') return { mult: 2, rule: 'v2', name: 'BTC 2 legs V2', cls: 'desk-v2' };
    if (id === 'son-btc-2legs-v2-newnew-a') return { mult: 2, rule: 'v2 newnew', name: 'V2 newnew A · abort', cls: 'desk-na' };
    if (id === 'son-btc-5m-ultimate-live') return { mult: 1, rule: 'v2 5m btc', name: '終極5m', cls: 'desk-5m' };
    return null;"""
    if old_meta not in text:
        raise SystemExit("deskBotMeta block missing")
    text = text.replace(old_meta, new_meta)
    text = text.replace(
        "const inputId = m.cls === 'desk-v2' ? 'bet-size-v2' : 'bet-size-sonu';",
        "const inputId = m.cls === 'desk-v2' ? 'bet-size-v2' : (m.cls === 'desk-na' ? 'bet-size-na' : 'bet-size-sonu');",
    )
    text = text.replace(
        "const name = botId === 'dual-leg-live-v2' ? 'BTC 2 legs V2' : '終極5m';",
        "const name = (deskBotMeta({id: botId}) || {}).name || botId;",
    )
    text = text.replace(
        '<div class="hl-title">Live · Son U</div>',
        '<div class="hl-title">Live</div>',
    )
    old_pill = """        const livePill = r.live_enabled
          ? '<span class="pill live">LIVE</span>'
          : ((r.had_live || r.live_tag === '曾live')
            ? '<span class="chip">曾live</span>'
            : '');"""
    new_pill = """        const lampOn = !!r.live_enabled;
        const livePill = '<span class="hl-badge ' + (lampOn ? 'is-on' : 'is-off') + '" aria-label="' + (lampOn ? 'LIVE ON' : '真倉 OFF') + '">' + (lampOn ? 'LIVE ON' : 'PAPER') + '</span>';"""
    if old_pill not in text:
        raise SystemExit("livePill block missing")
    text = text.replace(old_pill, new_pill)
    old_fold = """        if (String(r.id || '') === 'dual-leg-live-v2') {
          const v2Title = esc(r.display_name || 'BTC 2 legs V2 · 曾live');
          return `<details class="fam-fold desk-v2-fold" id="desk-v2-fold"><summary><span class="fam-fold-title">${v2Title}</span><span class="fam-fold-hint"> · 已摺 · 唔開 V2 live</span></summary><div class="fam-fold-inner">${card}</div></details>`;
        }
        return card;"""
    if old_fold not in text:
        raise SystemExit("v2 fold missing")
    text = text.replace(old_fold, "        return card;")
    old_prep = ".desk-bot-actions .hl-toggle.is-prep { border-color:rgba(251,191,36,.75); background:rgba(120,70,20,.55); }"
    new_prep = """/* Active mode on all 3 Live-box bots: same green lamp as V2 newnew A */
.desk-bot-actions button.hl-toggle.is-active,
.desk-bot-actions button.hl-toggle.is-prep {
  color: #ffffff;
  -webkit-text-fill-color: #ffffff;
  border-color: #39ff7a;
  background: radial-gradient(circle at 16% 50%, rgba(210,255,225,.55), rgba(0,204,68,.15) 36%), #00b83c;
  text-shadow: 0 0 2px #b6ffd0, 0 0 8px #00ff66, 0 0 16px #00ff66;
  box-shadow: 0 0 8px rgba(0,255,102,.75), 0 0 18px rgba(0,255,102,.45), inset 0 0 10px rgba(190,255,210,.35);
}
.desk-bot-actions button.hl-toggle.is-active::before,
.desk-bot-actions button.hl-toggle.is-prep::before {
  border-color: #eafff3;
  background: #ffffff;
  box-shadow: 0 0 4px #ffffff, 0 0 8px #00ff66, 0 0 16px #00ff00;
}"""
    if old_prep in text and "same green lamp as V2 newnew A" not in text:
        text = text.replace(old_prep, new_prep, 1)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_board()
    patch_template()
    print("three-bot live box patched")
