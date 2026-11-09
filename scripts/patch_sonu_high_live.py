from pathlib import Path

path = Path("/root/btc-5m-bot/dashboard/paper_board.py")
text = path.read_text(encoding="utf-8")

helper = '''

def _sonu_live_truth_module():
    try:
        import sonu_live_truth as mod
    except ImportError:
        import dashboard.sonu_live_truth as mod
    return mod


def _apply_sonu_on_air_high_live(root: Path, rows_out: list, wallet: float | None) -> bool:
    """When Son U exec is on-air, fix high_live row and return any_live."""
    mod = _sonu_live_truth_module()
    on_air = bool(mod.live_active(root))
    if not on_air:
        return any(bool(r.get("live_enabled")) for r in rows_out)
    stake = "10"
    for i, row in enumerate(rows_out):
        if str(row.get("id") or "") != "son-btc-5m-ultimate-live":
            continue
        cap = round(float(wallet), 2) if wallet is not None else float(row.get("capital_usd") or 100.52)
        rows_out[i] = {
            **row,
            "id": "son-btc-5m-ultimate-live",
            "display_name": "終極5m",
            "live_enabled": True,
            "mode": "live",
            "live_armed": False,
            "launched": True,
            "dry_run": False,
            "status": "RUNNING · live",
            "status_label": "跑緊 · 真倉 ON",
            "change": "Live · 真倉 ON · $10/注",
            "note": "Live · 真倉 ON · $10/注",
            "capital_usd": cap,
            "stake_usd": stake,
        }
        break
    return True

'''

if "_sonu_live_truth_module" not in text:
    anchor = "def _high_live_board(root: Path) -> dict[str, Any]:"
    if anchor not in text:
        raise SystemExit("anchor missing")
    text = text.replace(anchor, helper + anchor, 1)

ret = '''    return {
        "ok": True,
        "title": "Live",
        "subtitle": (
            "真倉 ON" if any_live
            else "預備區 · 紙上 rehearsal · 真倉 OFF（要錢包≥$100 +「Son U go live」）"
        ),
        "any_live": any_live,
        "wallet_cash_usdc": wallet,
        "rows": rows_out,
        "blocked": blocked,
    }'''

new_ret = '''    any_live = _apply_sonu_on_air_high_live(root, rows_out, wallet)
    if any_live:
        blocked = None
    return {
        "ok": True,
        "title": "Live",
        "subtitle": (
            "真倉 ON" if any_live
            else "預備區 · 紙上 rehearsal · 真倉 OFF（要錢包≥$100 +「Son U go live」）"
        ),
        "any_live": any_live,
        "wallet_cash_usdc": wallet,
        "rows": rows_out,
        "blocked": blocked,
    }'''

if ret not in text:
    raise SystemExit("return block missing")
text = text.replace(ret, new_ret, 1)

path.write_text(text, encoding="utf-8")
print("patched OK")
