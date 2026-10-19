"""Shared data models used by backtest, paper and live environments."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Stage(str, Enum):
    """Strategy lifecycle: research (backtest) -> paper -> live -> retired."""

    RESEARCH = "research"
    PAPER = "paper"
    LIVE = "live"
    RETIRED = "retired"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


# Timeframe -> seconds
TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

SECONDS_PER_YEAR = 365 * 86400


@dataclass(frozen=True)
class Bar:
    """A closed candle. ts is the bar open time (epoch ms, UTC)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: str
    timeframe: str


@dataclass(frozen=True)
class Signal:
    """Trade intent emitted by a strategy: side and size only, never touches the exchange."""

    side: Side
    qty: float  # base asset quantity
    reason: str = ""


@dataclass
class Order:
    ts: int
    symbol: str
    side: Side
    qty: float
    bot: str
    reason: str = ""


@dataclass(frozen=True)
class Fill:
    ts: int
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float  # in quote currency
    bot: str
    realized_pnl: float = 0.0  # realized PnL vs average cost (on position-reducing fills)


@dataclass(frozen=True)
class AccountView:
    """Read-only account snapshot as seen by a strategy."""

    cash: float
    position_qty: float
    avg_cost: float
    last_price: float

    @property
    def position_value(self) -> float:
        return self.position_qty * self.last_price

    @property
    def equity(self) -> float:
        return self.cash + self.position_value
