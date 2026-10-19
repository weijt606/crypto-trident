"""Strategy registry."""
from __future__ import annotations

from strategies.base import Strategy
from strategies.sma_cross import SmaCross

STRATEGIES: dict[str, type[Strategy]] = {
    "sma_cross": SmaCross,
}


def build_strategy(config: dict) -> Strategy:
    """Instantiate a strategy from a bot config."""
    strategy = config["strategy"]
    if strategy not in STRATEGIES:
        raise KeyError(f"unknown strategy {strategy!r}; available: {sorted(STRATEGIES)}")
    bot = STRATEGIES[strategy](
        name=config["name"],
        symbol=config["symbol"],
        timeframe=config["timeframe"],
        params=config.get("params", {}),
    )
    # Market type: spot (default) or perp (USDT-margined perpetual, can short/lever).
    # This is a property of the instrument, not an environment branch, so the
    # design rule "same strategy code in every environment" still holds.
    bot.market = config.get("market", "spot")
    return bot
