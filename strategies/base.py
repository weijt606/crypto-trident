"""Strategy base class. A strategy does one thing: consume bars, emit signals.

Design rule: the same strategy code runs in backtest, paper and live. All
environment differences belong to the execution layer (backtest engine,
paper trader, live executors), never to the strategy.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models import AccountView, Bar, Signal


class Strategy(ABC):
    def __init__(self, name: str, symbol: str, timeframe: str, params: dict):
        self.name = name
        self.symbol = symbol
        self.timeframe = timeframe
        self.params = {**self.default_params(), **(params or {})}
        # Composite sentiment score in [-100, 100], injected by the execution
        # layer using only data available at bar time (no lookahead).
        # None when unavailable; strategies that ignore sentiment can skip it.
        self.sentiment: float | None = None
        # Sum of funding rates settled since the previous bar (settled values
        # only, no lookahead), injected by the execution layer. On 1d bars this
        # is the daily funding sum. None when unavailable.
        self.funding: float | None = None
        # Market type spot/perp, overridden from config by build_strategy()
        self.market: str = "spot"

    @classmethod
    def default_params(cls) -> dict:
        return {}

    @property
    def warmup(self) -> int:
        """Number of historical bars needed before the strategy emits signals."""
        return 0

    # Set False if replaying history on startup would corrupt state (e.g. inventory-based
    # strategies); live/paper executors then start cold instead of warming up.
    warmup_replay: bool = True

    def describe(self) -> dict:
        """Snapshot of internal state + trigger conditions (shown on the dashboard).

        Subclasses should return {"state": {...}, "triggers": [{"condition", "level", "note"}, ...]}.
        """
        return {"state": {}, "triggers": []}

    @abstractmethod
    def on_bar(self, bar: Bar, account: AccountView) -> list[Signal]:
        """Receive a closed bar and return trade intents (possibly empty).

        Constraints:
        - never access the exchange, network or system clock; keep your own state
        - account is a read-only snapshot; whether an order is placed is decided
          by the risk guard and the execution layer
        """
