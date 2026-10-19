"""Market data download via the exchange public REST API (no API key needed)."""
from __future__ import annotations

import ccxt
import pandas as pd

BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def make_exchange(exchange_id: str = "binance") -> ccxt.Exchange:
    return getattr(ccxt, exchange_id)({"enableRateLimit": True})


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1h",
    days: int = 365,
    exchange_id: str = "binance",
) -> pd.DataFrame:
    """Page through historical candles (closed bars only)."""
    ex = make_exchange(exchange_id)
    since = ex.milliseconds() - days * 86_400_000
    rows: list[list] = []
    while True:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + 1
        if len(batch) < 1000:
            break
    df = (
        pd.DataFrame(rows, columns=BAR_COLUMNS)
        .drop_duplicates("ts")
        .sort_values("ts")
        .reset_index(drop=True)
    )
    # The last bar may still be open; drop it
    if len(df) > 0:
        tf_ms = ex.parse_timeframe(timeframe) * 1000
        now = ex.milliseconds()
        df = df[df["ts"] + tf_ms <= now].reset_index(drop=True)
    return df


def fetch_latest_closed_bar(
    ex: ccxt.Exchange, symbol: str, timeframe: str
) -> list | None:
    """Latest closed candle, used by paper-trading polling. Returns [ts,o,h,l,c,v] or None."""
    batch = ex.fetch_ohlcv(symbol, timeframe, limit=3)
    if not batch:
        return None
    tf_ms = ex.parse_timeframe(timeframe) * 1000
    now = ex.milliseconds()
    closed = [b for b in batch if b[0] + tf_ms <= now]
    return closed[-1] if closed else None
