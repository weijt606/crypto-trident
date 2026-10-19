"""Sentiment predictive-power check: IC + quantile returns, in-sample and out-of-sample.

Design rule: a sentiment indicator must pass this gate (|IC| >= 0.03 and monotonic
quantiles) before any strategy trades on it. The report is saved to storage/reports/.

Usage:
    uv run python scripts/validate_sentiment.py --symbol BTC/USDT
"""
import _bootstrap  # noqa: F401
import argparse

from data.store import load_bars
from monitor.logger import get_logger
from monitor.report import save_report
from sentiment.store import load_composite
from sentiment.validate import ic_table, quantile_table, verdict

log = get_logger("validate")


def main() -> None:
    p = argparse.ArgumentParser(description="Validate the predictive power of the sentiment composite.")
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--oos", type=float, default=0.3)
    args = p.parse_args()

    composite = load_composite()
    if composite is None or composite.empty:
        log.error("no composite sentiment data; run scripts/fetch_sentiment.py --backfill first")
        return
    bars = load_bars(args.symbol, "1d")[["ts", "close"]]

    split_ts = bars["ts"].iloc[int(len(bars) * (1 - args.oos))]
    segments = {
        "In-sample": bars[bars["ts"] < split_ts],
        "Out-of-sample": bars[bars["ts"] >= split_ts],
    }

    parts = [f"# Sentiment predictive-power check - {args.symbol}\n"]
    for label, seg in segments.items():
        ic = ic_table(composite, seg)
        qt = quantile_table(composite, seg, horizon=7)
        v = verdict(ic)
        log.info("%s: %s", label, v)
        parts += [
            f"## {label} ({len(seg)} days)\n",
            "### IC (Spearman, sentiment vs forward return)\n",
            ic.to_markdown(index=False), "",
            "### Sentiment quintile x 7-day forward return\n",
            qt.to_markdown(index=False) if len(qt) else "_not enough samples_", "",
            f"**Verdict**: {v}\n",
        ]

    report = "\n".join(parts)
    path = save_report(report, "sentiment_validation")
    log.info("report saved: %s", path)
    print("\n" + report)


if __name__ == "__main__":
    main()
