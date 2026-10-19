"""Polling throttle: bounds request frequency to public exchange APIs so the
client never produces an abusive request pattern.

Two principles:
1. Interval scales with the bar period. A 1d strategy gets one bar per day, so
   polling it every few seconds would be wasteful. A 15s floor keeps short
   timeframes responsive; a 300s cap means 1h+ timeframes poll at most every 5 min.
2. Exponential backoff on failure. During network jitter or rate limiting,
   double the delay up to a cap and reset on success, giving the server room
   and avoiding a request pile-up that looks like abuse.
"""
from __future__ import annotations

import random

from core.models import TIMEFRAME_SECONDS

POLL_FLOOR = 15        # fastest: 15s (5m/15m timeframes)
POLL_CAP = 300         # slowest: 5 min (1h and above)
BACKOFF_CAP = 600      # failure backoff cap: 10 min


def base_interval(timeframe: str) -> float:
    """Normal poll interval: ~1/10 of the bar period, clamped to [15s, 300s]."""
    tf_sec = TIMEFRAME_SECONDS.get(timeframe, 3600)
    return max(POLL_FLOOR, min(tf_sec // 10, POLL_CAP))


def next_sleep(timeframe: str, consecutive_errors: int) -> float:
    """Seconds to sleep this round: base interval + jitter when healthy,
    exponential backoff after errors.

    Jitter de-synchronizes multiple bots running in parallel so they do not
    hit the API at the same instant.
    """
    base = base_interval(timeframe)
    if consecutive_errors > 0:
        return min(base * (2 ** consecutive_errors), BACKOFF_CAP)
    return base + random.uniform(0, base * 0.1)
