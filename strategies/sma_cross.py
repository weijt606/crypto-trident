"""sma_cross: textbook dual moving-average trend strategy (spot, long only).

Fast SMA crosses above slow SMA -> buy with `alloc` of equity; crosses below
-> close the position. Defaults are the classic 50/200 "golden cross". Mainly
useful as an example and as a naive baseline to compare other strategies against.
"""
from __future__ import annotations

from collections import deque

from core.models import AccountView, Bar, Side, Signal
from strategies.base import Strategy


class SmaCross(Strategy):
    @classmethod
    def default_params(cls) -> dict:
        return {"fast": 50, "slow": 200, "alloc": 0.5}

    def __init__(self, name: str, symbol: str, timeframe: str, params: dict):
        super().__init__(name, symbol, timeframe, params)
        slow = self.params["slow"]
        if self.params["fast"] >= slow:
            raise ValueError("fast period must be smaller than slow")
        self._closes: deque[float] = deque(maxlen=slow)
        self._prev_diff: float | None = None

    @property
    def warmup(self) -> int:
        return self.params["slow"]

    def _sma(self, n: int) -> float:
        window = list(self._closes)[-n:]
        return sum(window) / len(window)

    def describe(self) -> dict:
        if len(self._closes) < self.params["slow"]:
            return {"state": {"status": f"warming up {len(self._closes)}/{self.params['slow']}"},
                    "triggers": []}
        fast, slow = self._sma(self.params["fast"]), self._sma(self.params["slow"])
        return {
            "state": {
                f"SMA{self.params['fast']}": round(fast, 2),
                f"SMA{self.params['slow']}": round(slow, 2),
                "fast_minus_slow": round(fast - slow, 2),
                "trend": "up" if fast > slow else "down",
            },
            "triggers": [
                {"condition": "BUY", "level": None, "note": "fast crosses above slow while flat"},
                {"condition": "SELL all", "level": None, "note": "fast crosses below slow while long"},
            ],
        }

    def on_bar(self, bar: Bar, account: AccountView) -> list[Signal]:
        self._closes.append(bar.close)
        if len(self._closes) < self.params["slow"]:
            return []

        diff = self._sma(self.params["fast"]) - self._sma(self.params["slow"])
        prev, self._prev_diff = self._prev_diff, diff
        if prev is None:
            return []

        signals: list[Signal] = []
        if prev <= 0 < diff and account.position_qty == 0:
            qty = account.equity * self.params["alloc"] / bar.close
            signals.append(Signal(Side.BUY, qty, reason="golden cross"))
        elif prev >= 0 > diff and account.position_qty > 0:
            signals.append(Signal(Side.SELL, account.position_qty, reason="death cross"))
        return signals
