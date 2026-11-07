#!/usr/bin/env python3
"""Gold-box funnel: 考慮 and 過濾 show the same count, so keep 過濾 only."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/btc-5m-bot")


def main() -> None:
    path = ROOT / "dashboard" / "templates" / "v1_only.html"
    text = path.read_text(encoding="utf-8")

    old_css = ".hl-window-funnel {\n  display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:.4rem;"
    new_css = ".hl-window-funnel {\n  display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.4rem;"
    if old_css in text:
        text = text.replace(old_css, new_css, 1)
        print("css updated")
    elif "repeat(2,minmax(0,1fr)); gap:.4rem;" in text:
        print("css already")
    else:
        raise SystemExit("funnel css missing")

    old_js = """        const winC = (wf.considered != null ? wf.considered : (r.scan_count != null ? r.scan_count : '—'));
        const winF = (wf.filtered != null ? wf.filtered : '—');"""
    new_js = """        const winF = (wf.filtered != null ? wf.filtered : (wf.considered != null ? wf.considered : (r.scan_count != null ? r.scan_count : '—')));"""
    if old_js in text:
        text = text.replace(old_js, new_js, 1)
        print("count updated")
    elif "const winF = (wf.filtered != null ? wf.filtered : (wf.considered" in text:
        print("count already")
    else:
        raise SystemExit("funnel counts missing")

    old_html = """          <div class="hl-window-funnel" aria-label="窗口考慮／過濾／入場">
            <div class="cell"><div class="k">考慮</div><div class="v">${esc(String(winC))}</div></div>
            <div class="cell"><div class="k">過濾</div><div class="v filt">${esc(String(winF))}</div></div>
            <div class="cell"><div class="k">入場</div><div class="v ent">${esc(String(winE))}</div></div>
          </div>"""
    new_html = """          <div class="hl-window-funnel" aria-label="窗口過濾／入場">
            <div class="cell"><div class="k">過濾</div><div class="v filt">${esc(String(winF))}</div></div>
            <div class="cell"><div class="k">入場</div><div class="v ent">${esc(String(winE))}</div></div>
          </div>"""
    if old_html in text:
        text = text.replace(old_html, new_html, 1)
        print("cells updated")
    elif 'aria-label="窗口過濾／入場"' in text:
        print("cells already")
    else:
        raise SystemExit("funnel cells missing")

    path.write_text(text, encoding="utf-8")
    print("written", path)


if __name__ == "__main__":
    main()
