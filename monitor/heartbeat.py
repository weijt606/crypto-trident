"""Bot heartbeat: proves an executor process is alive right now.

last_ts (the latest bar) is naturally ~24h old for a 1d strategy, so it cannot
tell whether the process is running. The heartbeat is refreshed on every poll,
which lets the dashboard distinguish "alive but no trades" from "stuck".
"""
from __future__ import annotations

import time
from pathlib import Path

from core.config import storage_dir


def _dir() -> Path:
    d = storage_dir() / "heartbeats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def beat(name: str) -> None:
    """Called by executors on every poll; writes the current epoch ms."""
    (_dir() / f"{name}.txt").write_text(str(int(time.time() * 1000)))


def last_beat(name: str) -> int | None:
    """Latest heartbeat for a bot in epoch ms, or None if there is none."""
    f = _dir() / f"{name}.txt"
    if not f.exists():
        return None
    try:
        return int(f.read_text().strip())
    except (ValueError, OSError):
        return None
