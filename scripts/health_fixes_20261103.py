"""Afternoon health fixes: mode chip, OOM priority, stopped-son dedupe, funding scan archive."""
from __future__ import annotations

import json
import tarfile
from collections import OrderedDict
from pathlib import Path

ROOT = Path("/root/btc-5m-bot")
TPL = ROOT / "dashboard" / "templates" / "v1_only.html"


def patch_chip() -> None:
    text = TPL.read_text(encoding="utf-8")
    if "function paintModeChip" not in text:
        helper = """
  function paintModeChip(live) {
    const chip = $('mode-chip');
    if (!chip) return;
    if (live) {
      chip.textContent = 'LIVE';
      chip.className = 'chip ok';
      chip.style.background = '#ff0000';
      chip.style.borderColor = '#ff4444';
      chip.style.color = '#fff';
    } else {
      chip.textContent = 'PAPER';
      chip.className = 'chip ok';
      chip.style.background = '';
      chip.style.borderColor = '';
      chip.style.color = '';
    }
  }
"""
        anchor = "  function applyLiveTruthFromApis(liveCtrl, deskPayload) {"
        if anchor not in text:
            raise SystemExit("applyLiveTruth missing")
        text = text.replace(anchor, helper + anchor, 1)
    old = """    const chip = $('mode-chip');
    if (chip && _sonuLiveTruth) {
      if (_sonuLiveTruth.live_active) {
        chip.textContent = 'LIVE';
        chip.className = 'chip ok';
        chip.style.background = '#ff0000';
        chip.style.borderColor = '#ff4444';
        chip.style.color = '#fff';
      } else {
        chip.textContent = 'PAPER';
        chip.className = 'chip ok';
        chip.style.background = '';
        chip.style.borderColor = '';
        chip.style.color = '';
      }
    }
  }"""
    new = """    paintModeChip(!!(_sonuLiveTruth && _sonuLiveTruth.live_active));
  }"""
    if old in text:
        text = text.replace(old, new, 1)
    needle = """    if (badge) {
      if (anyLive) {
        badge.hidden = false;
        badge.textContent = 'LIVE ON';"""
    insert = """    const sonLive = rows.some((r) => String(r.id || '') === 'son-btc-5m-ultimate-live' && !!r.live_enabled);
    paintModeChip(!!(_sonuLiveTruth && _sonuLiveTruth.live_active) || sonLive);
    if (badge) {
      if (anyLive) {
        badge.hidden = false;
        badge.textContent = 'LIVE ON';"""
    if "const sonLive = rows.some" not in text:
        if needle not in text:
            raise SystemExit("badge block missing")
        text = text.replace(needle, insert, 1)
    TPL.write_text(text, encoding="utf-8")
    print("chip patched")


def dedupe_stopped(path: Path) -> None:
    if not path.is_file():
        return
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        return
    kept: OrderedDict[str, dict] = OrderedDict()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        key = str(row.get("slug") or row.get("id") or f"row:{index}")
        kept[key] = row
    if len(kept) == len(rows):
        print(path.name, "no dups")
        return
    backup = path.with_suffix(path.suffix + ".bak-dedupe-20261103")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_text(json.dumps(list(kept.values()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path.name, len(rows), "->", len(kept))


def archive_scans() -> None:
    scan_dir = Path("/root/funding-carry-bot/logs/scans")
    archive = Path("/root/funding-carry-bot/logs/scans_archive_before_20261101.tar.gz")
    if archive.exists():
        print("scan archive exists", archive.stat().st_size)
        return
    old = sorted(p for p in scan_dir.glob("scan_*.json") if p.name < "scan_20261101")
    if not old:
        print("no old scans")
        return
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        for path in old:
            tar.add(path, arcname=path.name)
    for path in old:
        path.unlink()
    print("archived", len(old), "scans ->", archive)


if __name__ == "__main__":
    patch_chip()
    for letter in "abe":
        dedupe_stopped(ROOT / "logs" / "sons" / f"son-btc-2legs-v2-{letter}_trades.json")
    archive_scans()
