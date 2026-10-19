"""Outbound IP lookup and masking.

Kept in its own lightweight module so long-running processes can use it
without pulling in pandas/pyarrow via the executors.
"""
from __future__ import annotations

import re


def mask_ip(ip: str) -> str:
    """a.b.c.d -> a.b.x.x. Mask any IP sent to alerts (which may leave the host,
    e.g. Telegram) or written to the event table; the full IP only goes to the
    local log so the operator can add it to the exchange API whitelist."""
    m = re.match(r"^(\d+)\.(\d+)\.\d+\.\d+$", ip.strip())
    return f"{m.group(1)}.{m.group(2)}.x.x" if m else ip


def _outbound_ip() -> str:
    """Current public outbound IP, included in auth-failure alerts so the
    operator can whitelist it directly."""
    try:
        import urllib.request
        return urllib.request.urlopen("https://api.ipify.org", timeout=5).read().decode().strip()
    except Exception:  # noqa: BLE001
        return "(lookup failed; try `curl ifconfig.me`)"
