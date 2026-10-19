"""Sentiment pipeline: fetch enabled sources -> store -> rebuild the composite -> print a snapshot.

Run on a timer (hourly is enough). A failing source never blocks the others.

Usage:
    uv run python scripts/fetch_sentiment.py            # incremental update
    uv run python scripts/fetch_sentiment.py --backfill # first run: backfill all available history
"""
import _bootstrap  # noqa: F401
import argparse

from monitor.logger import get_logger
from sentiment import sources
from sentiment.composite import Z_MIN_PERIODS, build_composite, regime
from sentiment.store import load_source, save_composite, save_source

log = get_logger("sentiment")


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch sentiment sources and rebuild the composite.")
    p.add_argument("--backfill", action="store_true",
                   help="backfill full Fear & Greed history and multi-year funding rates")
    args = p.parse_args()

    fetched = sources.fetch_enabled(backfill=args.backfill)  # sources enabled in config
    for name, df in fetched.items():
        path = save_source(name, df)
        log.info("%-11s fetched %4d rows%s", name, len(df), f" -> {path.name}" if path else " (not stored)")

    stored = {name: load_source(name) for name in fetched}
    composite = build_composite(stored)
    if composite.empty:
        log.warning("composite is empty (needs at least %d days of history to standardise)", Z_MIN_PERIODS)
        return
    save_composite(composite)

    latest = composite.iloc[-1]
    log.info(
        "composite sentiment: score=%.1f (%s) | %d sources | divergence %.2f | %d days",
        latest["score"], regime(latest["score"]),
        int(latest["n_sources"]), latest["divergence"], len(composite),
    )


if __name__ == "__main__":
    main()
