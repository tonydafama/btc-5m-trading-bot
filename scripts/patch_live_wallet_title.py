#!/usr/bin/env python3
"""Show the real wallet on the same line as the Live title."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/btc-5m-bot")

OLD_TITLE = '<div class="hl-title">Live</div>'
NEW_TITLE = '<div class="hl-title">Live <span id="hl-wallet" class="hl-wallet"></span></div>'

OLD_CSS = ".hl-wrap .hl-title { font-size: 1.02rem; font-weight: 750; color: #f0d9a8; }"
NEW_CSS = """.hl-wrap .hl-title { font-size: 1.02rem; font-weight: 750; color: #f0d9a8; }
.hl-wrap .hl-wallet {
  margin-left: .7rem;
  font-size: .86rem;
  font-weight: 750;
  letter-spacing: 0;
  color: #d5deea;
}"""

OLD_NOTE = """    const bits = [];
    if (hl.wallet_cash_usdc != null) bits.push('真錢包 $' + Number(hl.wallet_cash_usdc).toFixed(2));
    if (hl.blocked) bits.push('阻擋：' + hl.blocked);"""
NEW_NOTE = """    const walletEl = $('hl-wallet');
    if (walletEl) {
      walletEl.textContent = hl.wallet_cash_usdc != null
        ? ('真錢包 $' + Number(hl.wallet_cash_usdc).toFixed(2))
        : '';
    }
    const bits = [];
    if (hl.blocked) bits.push('阻擋：' + hl.blocked);"""


def main() -> None:
    path = ROOT / "dashboard" / "templates" / "v1_only.html"
    text = path.read_text(encoding="utf-8")
    if 'id="hl-wallet"' not in text:
        if OLD_TITLE not in text:
            raise SystemExit("Live title missing")
        text = text.replace(OLD_TITLE, NEW_TITLE, 1)
        print("title updated")
    else:
        print("title already")
    if ".hl-wrap .hl-wallet" not in text:
        if OLD_CSS not in text:
            raise SystemExit("title css missing")
        text = text.replace(OLD_CSS, NEW_CSS, 1)
        print("css updated")
    else:
        print("css already")
    if "const walletEl = $('hl-wallet')" not in text:
        if OLD_NOTE not in text:
            raise SystemExit("wallet note missing")
        text = text.replace(OLD_NOTE, NEW_NOTE, 1)
        print("note updated")
    else:
        print("note already")
    path.write_text(text, encoding="utf-8")
    print("written", path)


if __name__ == "__main__":
    main()
