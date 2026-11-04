from pathlib import Path

p = Path("/root/btc-5m-bot/dashboard/templates/v1_only.html")
text = p.read_text(encoding="utf-8")
css = """
/* Live gold box: one type scale and one lamp style for all 3 bots */
.hl-wrap .hl-bot {
  font-family: var(--sans);
  border: 1px solid rgba(224,181,103,.35);
  border-radius: 12px;
  padding: .7rem .7rem .75rem;
  background: rgba(8,10,14,.35);
}
.hl-wrap .hl-head { display: block; }
.hl-wrap .hl-name-row { display: flex; gap: .4rem; align-items: center; flex-wrap: wrap; }
.hl-wrap .hl-bot .hl-name {
  font-family: var(--sans);
  font-size: .92rem;
  font-weight: 750;
  letter-spacing: .01em;
  line-height: 1.2;
  color: #f4f7fb;
}
.hl-wrap .hl-bot .hl-subline {
  margin-top: .22rem;
  min-height: 1.35em;
  font-family: var(--sans);
  font-size: .72rem;
  font-weight: 500;
  line-height: 1.35;
  letter-spacing: 0;
  color: #9aa6b6;
}
.hl-wrap .hl-bot .hl-badge {
  font-family: var(--sans);
  font-size: .68rem;
  font-weight: 800;
  letter-spacing: .08em;
}
.hl-wrap .desk-bot-actions button.hl-toggle,
.hl-wrap .desk-bot-actions button.hl-toggle .mode-label {
  font-family: var(--sans);
  font-size: .72rem;
  font-weight: 750;
  letter-spacing: .01em;
  line-height: 1.2;
}
.hl-wrap .bet-size-box,
.hl-wrap .bet-size-box label,
.hl-wrap .bet-size-readout,
.hl-wrap .hl-trades-line,
.hl-wrap .hl-window-funnel .k,
.hl-wrap .hl-stat .k {
  font-family: var(--sans);
}
.hl-wrap .hl-window-funnel .v,
.hl-wrap .hl-stat .v,
.hl-wrap .bet-size-row input.bet-size-input {
  font-family: var(--mono);
}
"""
anchor = ".hl-wrap .hl-title { font-size: 1.02rem; font-weight: 750; color: #f0d9a8; }"
if "one type scale and one lamp style" not in text:
    if anchor not in text:
        raise SystemExit("title anchor missing")
    text = text.replace(anchor, anchor + "\n" + css, 1)

old = """        const card = `<div class="son${_dm ? " desk-split-bot" : ""}"${_deskAttr} style="border-color:rgba(224,181,103,.35);display:block">
          <div style="display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap;align-items:flex-start">
            <div>
              <div style="display:flex;gap:.4rem;align-items:center;flex-wrap:wrap">
                <strong>${esc(r.display_name || r.id || '—')}</strong>
                ${livePill}
              </div>
              <div class="meta" style="margin-top:.2rem">${esc(r.status_label || r.status || '')} · ${esc(stake)} · 本金 ${esc(cap)}</div>
              <div class="meta" style="margin-top:.15rem">${esc(shortChange(r))}</div>
            </div>
          </div>"""
new = """        const card = `<div class="son hl-bot${_dm ? " desk-split-bot" : ""}"${_deskAttr}>
          <div class="hl-head">
            <div class="hl-name-row">
              <span class="hl-name">${esc(r.display_name || r.id || '—')}</span>
              ${livePill}
            </div>
            <div class="hl-subline">${esc(r.status_label || r.status || '')} · ${esc(stake)} · 本金 ${esc(cap)}</div>
            <div class="hl-subline">${esc(shortChange(r))}</div>
          </div>"""
if old not in text:
    if "hl-name-row" in text:
        print("markup already aligned")
    else:
        raise SystemExit("card markup missing")
else:
    text = text.replace(old, new, 1)
p.write_text(text, encoding="utf-8")
print("aligned")
