"""Multi-bot instance paths and env bootstrap (Bot #2 = Alpha-Max live)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path("/root/btc-5m-bot")


def instance_name() -> str:
    return os.getenv("BOT_INSTANCE", "main").strip().lower() or "main"


def log_root() -> Path:
    inst = instance_name()
    if inst in ("main", "1", "bot1", ""):
        return ROOT / "logs"
    return ROOT / "logs" / inst


def env_file() -> Path:
    explicit = os.getenv("BOT_ENV_FILE", "").strip()
    if explicit:
        return Path(explicit)
    inst = instance_name()
    if inst in ("main", "1", "bot1", ""):
        return ROOT / ".env"
    return ROOT / f".env.{inst}"


def bootstrap_instance(*, default_instance: str | None = None) -> Path:
    """Load instance .env and set wallet cache path. Call before other bot imports."""
    if default_instance and not os.getenv("BOT_INSTANCE"):
        os.environ["BOT_INSTANCE"] = default_instance
    path = env_file()
    if path.exists():
        from dotenv import load_dotenv

        load_dotenv(path, override=True)
    lr = log_root()
    lr.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("WALLET_CACHE_FILE", str(lr / "wallet_cache.json"))
    return lr
