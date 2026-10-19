"""Polling throttle tests: API request frequency stays bounded."""
from __future__ import annotations

from core.polling import BACKOFF_CAP, POLL_CAP, POLL_FLOOR, base_interval, next_sleep


def test_interval_scales_with_timeframe():
    assert base_interval("1m") == POLL_FLOOR     # 60//10=6, floored to 15
    assert base_interval("5m") == 30             # 300//10=30
    assert base_interval("15m") == 90            # 900//10=90
    assert base_interval("1h") == POLL_CAP       # 3600//10=360, capped 300
    assert base_interval("1d") == POLL_CAP       # capped 300
    assert base_interval("1d") >= base_interval("1h") >= base_interval("15m")


def test_1d_request_volume_bounded():
    # slow timeframes poll at most every POLL_CAP seconds
    per_day = 86400 / base_interval("1d")
    assert per_day <= 300                        # <=288 requests/day


def test_jitter_within_10pct_when_no_error():
    base = base_interval("1h")
    for _ in range(50):
        s = next_sleep("1h", 0)
        assert base <= s <= base * 1.1


def test_exponential_backoff_capped():
    base = base_interval("5m")
    assert next_sleep("5m", 1) == min(base * 2, BACKOFF_CAP)
    assert next_sleep("5m", 2) == min(base * 4, BACKOFF_CAP)
    assert next_sleep("5m", 20) == BACKOFF_CAP   # never exceeds cap


def test_unknown_timeframe_safe_default():
    assert POLL_FLOOR <= base_interval("99x") <= POLL_CAP
