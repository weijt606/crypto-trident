"""Startup warmup tests: indicators are rebuilt immediately after a restart, no orders."""
from __future__ import annotations

from collections import deque

import pandas as pd
import pytest

import execution.warmup as warmup_mod
from core.models import Side, Signal
from execution.warmup import warmup_strategy
from strategies.base import Strategy
from strategies.sma_cross import SmaCross


def _bars(n: int, tf: str, start_price: float = 100.0) -> pd.DataFrame:
    tf_ms = {"1h": 3600_000, "1d": 86400_000}[tf]
    rows = []
    for i in range(n):
        p = start_price + i
        rows.append({"ts": i * tf_ms, "open": p, "high": p + 1,
                     "low": p - 1, "close": p, "volume": 1.0})
    return pd.DataFrame(rows)


@pytest.fixture()
def fetch_calls(monkeypatch):
    """Block network and external files: synthetic bars, no sentiment, no funding file."""
    calls = {"n": 0}

    def fake_fetch(symbol, timeframe, days=365, exchange_id="binance"):
        calls["n"] += 1
        return _bars(120, timeframe)

    monkeypatch.setattr(warmup_mod, "fetch_ohlcv", fake_fetch)
    monkeypatch.setattr(warmup_mod, "load_composite", lambda: None)
    monkeypatch.setattr(warmup_mod, "funding_path",
                        lambda symbol: __import__("pathlib").Path("/nonexistent/funding.parquet"))
    return calls


class FundingWindow(Strategy):
    """Tiny strategy that keeps a rolling window of injected funding values."""

    def __init__(self):
        super().__init__("fund-t", "BTC/USDT", "1h", {})
        self._fund: deque[float] = deque(maxlen=5)

    @property
    def warmup(self) -> int:
        return 5

    def on_bar(self, bar, account):
        if self.funding is not None:
            self._fund.append(self.funding)
        return [Signal(Side.BUY, 1.0)]  # always signals; warmup must discard it


class Inventory(Strategy):
    """Strategy that mutates internal inventory in on_bar and opts out of replay."""

    warmup_replay = False

    def __init__(self):
        super().__init__("inv-t", "BTC/USDT", "1h", {})
        self.inventory = 0

    @property
    def warmup(self) -> int:
        return 10

    def on_bar(self, bar, account):
        self.inventory += 1
        return []


def test_sma_indicators_rebuilt(fetch_calls):
    bot = SmaCross("sma-t", "BTC/USDT", "1h", {"fast": 5, "slow": 20})
    last = warmup_strategy(bot)
    assert last is not None
    assert len(bot._closes) == bot.params["slow"]  # window is full
    assert "status" not in bot.describe()["state"]  # no longer "warming up"


def test_last_ts_is_latest_replayed_bar(fetch_calls):
    bot = SmaCross("sma-t", "BTC/USDT", "1h", {"fast": 5, "slow": 20})
    assert warmup_strategy(bot) == 119 * 3600_000


def test_zero_warmup_skipped(fetch_calls):
    class NoWarmup(Strategy):
        def on_bar(self, bar, account):
            raise AssertionError("must not be replayed")

    assert warmup_strategy(NoWarmup("nw-t", "BTC/USDT", "1h", {})) is None
    assert fetch_calls["n"] == 0


def test_opt_out_does_not_pollute_state(fetch_calls):
    bot = Inventory()
    assert warmup_strategy(bot) is None
    assert fetch_calls["n"] == 0
    assert bot.inventory == 0


def test_funding_window_rebuilt(monkeypatch, fetch_calls):
    fund = pd.DataFrame({"ts": [i * 3600_000 for i in range(120)],
                         "value": [0.0001] * 120})

    class _Existing:
        def exists(self):
            return True

    monkeypatch.setattr(warmup_mod, "funding_path", lambda symbol: _Existing())
    monkeypatch.setattr(warmup_mod.pd, "read_parquet", lambda *_: fund)
    bot = FundingWindow()
    warmup_strategy(bot)
    assert len(bot._fund) == 5  # funding window is full right after restart
    assert all(v > 0 for v in bot._fund)


def test_warmup_never_places_orders(fetch_calls):
    """Signals emitted during replay are discarded; warmup returns only a timestamp."""
    bot = FundingWindow()
    result = warmup_strategy(bot)
    assert isinstance(result, int)
