"""Startup warmup: replay recent history through a strategy to rebuild its internal state.

On restart the executors only restore cash/position from storage; a strategy's own
indicators (deques, moving averages, channels) start empty. For strategies with a long
warmup that would mean days or weeks without trading after every restart.

Warmup fetches the last ``warmup + margin`` closed bars, injects sentiment/funding with
the same no-lookahead rules as live trading, feeds each bar to ``on_bar`` and **discards
the returned signals** (warmup never trades). It returns the timestamp of the last
replayed bar so the executor can align ``last_bar_ts`` and not treat the replay window
as bars that still need to be traded.

Skipped when ``bot.warmup <= 0`` or when the strategy opts out with
``warmup_replay = False`` (for strategies whose ``on_bar`` mutates state that must not
be replayed, e.g. an internal inventory counter).
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from core.config import storage_dir
from core.models import TIMEFRAME_SECONDS, AccountView, Bar
from data.fetcher import fetch_ohlcv
from monitor.logger import get_logger
from strategies.base import Strategy
from sentiment.composite import as_of
from sentiment.store import load_composite

log = get_logger("warmup")


def funding_path(symbol: str) -> Path:
    """Settled funding-rate history file for a symbol's base asset.

    BTC uses ``funding.parquet`` (written by the sentiment pipeline); other bases use
    ``funding_<BASE>.parquet`` if present.
    """
    base = symbol.split("/")[0]
    fname = "funding.parquet" if base == "BTC" else f"funding_{base}.parquet"
    return storage_dir() / "sentiment" / fname


def warmup_strategy(bot: Strategy, exchange_id: str = "binance",
                    margin: int = 10) -> int | None:
    """Replay history to rebuild ``bot``'s indicators. Returns last bar ts or None."""
    need = bot.warmup
    if need <= 0 or not getattr(bot, "warmup_replay", True):
        return None

    n = need + margin
    tf_sec = TIMEFRAME_SECONDS.get(bot.timeframe, 3600)
    days = math.ceil(n * tf_sec / 86400) + 2
    try:
        bars = fetch_ohlcv(bot.symbol, bot.timeframe, days=days,
                           exchange_id=exchange_id)
    except Exception as e:
        log.warning("%s warmup fetch failed, continuing cold: %s", bot.name, e)
        return None
    if bars is None or not len(bars):
        return None
    bars = bars.tail(n)

    composite = load_composite()
    funding = None
    fpath = funding_path(bot.symbol)
    if fpath.exists():
        try:
            funding = pd.read_parquet(fpath)
        except Exception:
            funding = None

    prev_ts, last_ts = 0, None
    for row in bars.itertuples(index=False):
        bar = Bar(
            ts=int(row.ts), open=float(row.open), high=float(row.high),
            low=float(row.low), close=float(row.close), volume=float(row.volume),
            symbol=bot.symbol, timeframe=bot.timeframe,
        )
        # Same no-lookahead injection as live: sentiment by avail_ts <= bar.ts,
        # funding = sum of rates settled since the previous bar.
        if composite is not None and len(composite):
            score = as_of(composite, bar.ts)
            if score is not None:
                bot.sentiment = score
        if funding is not None:
            w = funding[(funding["ts"] > prev_ts) & (funding["ts"] <= bar.ts)]
            if len(w):
                bot.funding = float(w["value"].sum())
        # Rebuild internal state only; signals are dropped (warmup never orders).
        bot.on_bar(bar, AccountView(cash=0.0, position_qty=0.0,
                                    avg_cost=0.0, last_price=bar.close))
        prev_ts, last_ts = bar.ts, bar.ts

    log.info("%s warmup done: replayed %d %s bars, indicators rebuilt to latest close",
             bot.name, len(bars), bot.timeframe)
    return last_ts
