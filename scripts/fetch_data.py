"""Fetch historical OHLCV bars and store them locally (public endpoint, no API key).

Usage:
    uv run python scripts/fetch_data.py --symbol BTC/USDT --timeframe 1h --days 730
"""
import _bootstrap  # noqa: F401
import argparse

from data.fetcher import fetch_ohlcv
from data.store import save_bars
from monitor.logger import get_logger

log = get_logger("fetch")


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch historical OHLCV bars.")
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--exchange", default="binance")
    args = p.parse_args()

    log.info("fetching %s %s, last %d days ...", args.symbol, args.timeframe, args.days)
    df = fetch_ohlcv(args.symbol, args.timeframe, args.days, args.exchange)
    path = save_bars(df, args.symbol, args.timeframe, args.exchange)
    log.info("done: %d bars -> %s", len(df), path)


if __name__ == "__main__":
    main()
