#!/usr/bin/env python3
"""Backfill trade analytics fields on historical logs (Alpha live, V1 live, research jsonl)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def backfill_v1_live(path: Path) -> int:
    from trade_analytics import enrich_v1_row

    rows = _load_json(path, [])
    if not isinstance(rows, list):
        return 0
    changed = 0
    updated = []
    for row in rows:
        before = json.dumps(row, sort_keys=True, default=str)
        new_row = enrich_v1_row(row)
        if json.dumps(new_row, sort_keys=True, default=str) != before:
            changed += 1
        updated.append(new_row)
    if changed:
        _save_json(path, updated)
    return changed


def backfill_alpha_window_outcomes(*, instance: str = "bot2") -> int:
    os.environ.setdefault("BOT_INSTANCE", instance)
    from bot_instance import bootstrap_instance, log_root

    bootstrap_instance(default_instance=instance)
    from polymarket_btc import fetch_market_outcome

    root = log_root()
    updated = 0
    for name in ("alpha_max_btc_windows.json", "alpha_max_eth_windows.json"):
        path = root / name
        windows = _load_json(path, [])
        if not isinstance(windows, list):
            continue
        changed = False
        for rec in windows:
            if rec.get("outcome") in ("up", "down"):
                continue
            slug = str(rec.get("slug") or "")
            if not slug:
                continue
            resolution = fetch_market_outcome(slug)
            if not resolution or resolution.get("outcome") not in ("up", "down"):
                continue
            rec["outcome"] = resolution["outcome"]
            rec["outcome_source"] = "polymarket_backfill"
            changed = True
            updated += 1
        if changed:
            _save_json(path, windows)
    return updated


def backfill_alpha_windows_research(*, instance: str = "bot2") -> int:
    os.environ.setdefault("BOT_INSTANCE", instance)
    from bot_instance import bootstrap_instance, log_root

    bootstrap_instance(default_instance=instance)
    from alpha_max_research import RESEARCH_FILE, record_window_evaluation

    root = log_root()
    seen: set[str] = set()
    if RESEARCH_FILE.exists():
        for line in RESEARCH_FILE.read_text().splitlines():
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line).get("slug", ""))
            except json.JSONDecodeError:
                pass

    written = 0
    for asset, name in (("BTC", "alpha_max_btc_windows.json"), ("ETH", "alpha_max_eth_windows.json")):
        windows = _load_json(root / name, [])
        if not isinstance(windows, list):
            continue
        for rec in windows:
            slug = rec.get("slug") or ""
            if not slug or slug in seen:
                continue
            cps = rec.get("checkpoints") or []
            outcome = rec.get("outcome") or rec.get("outcome_estimate")
            if not cps or outcome not in ("up", "down"):
                continue
            record_window_evaluation(
                asset=asset,
                slug=slug,
                title=rec.get("title") or "",
                checkpoints=cps,
                outcome=outcome,
                source="live_backfill",
            )
            seen.add(slug)
            written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill trade analytics on historical logs")
    parser.add_argument("--instance", default="bot2", help="Bot instance for Alpha logs (default: bot2)")
    parser.add_argument("--force", action="store_true", help="Re-run Alpha backfill even if schema version matches")
    parser.add_argument("--skip-v1", action="store_true")
    parser.add_argument("--skip-alpha", action="store_true")
    parser.add_argument("--skip-research", action="store_true")
    parser.add_argument("--skip-window-outcomes", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("BOT_INSTANCE", args.instance)
    from bot_instance import bootstrap_instance

    bootstrap_instance(default_instance=args.instance)

    if not args.skip_alpha:
        from alpha_max_live import backfill_alpha_live_analytics

        n = backfill_alpha_live_analytics(force=args.force)
        print(f"Alpha live log rows updated: {n}")

    if not args.skip_v1:
        v1_paths = [ROOT / "logs" / "trade_log_live.json", ROOT / "logs" / "trade_log_eth_live.json"]
        for path in v1_paths:
            n = backfill_v1_live(path)
            print(f"{path.name} rows updated: {n}")

    if not args.skip_window_outcomes:
        n = backfill_alpha_window_outcomes(instance=args.instance)
        print(f"Alpha window outcomes backfilled: {n}")

    if not args.skip_research:
        n = backfill_alpha_windows_research(instance=args.instance)
        print(f"Alpha research jsonl windows appended: {n}")


if __name__ == "__main__":
    main()
