"""Backtest a bot on in-sample and out-of-sample segments and write a report.

Design rule: every report includes both segments; out-of-sample performance is the
promotion gate.

Usage:
    uv run python scripts/run_backtest.py --bot sma-demo
    uv run python scripts/run_backtest.py --bot sma-demo --oos 0.3
"""
import _bootstrap  # noqa: F401
import argparse

import pandas as pd

from backtest.engine import BacktestConfig, run_backtest
from core.config import load_bot_config, load_system_config, storage_dir
from data.store import load_bars
from monitor.logger import get_logger
from monitor.report import render_backtest_report, save_report
from safety.guard import RiskConfig
from strategies import build_strategy

log = get_logger("backtest")


def main() -> None:
    p = argparse.ArgumentParser(description="Run an in-sample / out-of-sample backtest.")
    p.add_argument("--bot", required=True, help="config name under config/bots/")
    p.add_argument("--oos", type=float, default=0.3,
                   help="out-of-sample fraction, cut from the end of the series (default 0.3)")
    args = p.parse_args()

    sys_cfg = load_system_config()
    bot_cfg = load_bot_config(args.bot)
    bars = load_bars(bot_cfg["symbol"], bot_cfg["timeframe"], sys_cfg["exchange"])
    log.info("loaded %d bars", len(bars))

    sentiment = None
    if bot_cfg.get("sentiment"):
        from sentiment.store import load_composite

        sentiment = load_composite()
        if sentiment is None or sentiment.empty:
            raise SystemExit("this strategy needs sentiment data; run scripts/fetch_sentiment.py --backfill first")
        log.info("loaded %d days of composite sentiment", len(sentiment))

    market = bot_cfg.get("market", "spot")
    funding = None
    if bot_cfg.get("funding") and market == "spot":
        # Spot strategies may read funding rates as a signal input.
        funding = pd.read_parquet(storage_dir() / "sentiment" / "funding.parquet")
        log.info("loaded %d funding-rate periods (signal input)", len(funding))
    risk_kwargs = dict(sys_cfg["risk"])
    fee_bps = sys_cfg["fees"]["taker_bps"]
    max_leverage = 1.0
    if market == "perp":
        # Perp risk overlay: allow shorts + leverage cap (hard cap 3).
        overlay = sys_cfg.get("risk_perp", {})
        risk_kwargs.update(allow_short=True,
                           max_leverage=min(float(overlay.get("max_leverage", 3)), 3.0))
        fee_bps = sys_cfg["fees"].get("perp_taker_bps", 5)
        max_leverage = risk_kwargs["max_leverage"]

        # Real funding history per coin: BTC uses funding.parquet, others funding_<BASE>.parquet.
        base = bot_cfg["symbol"].split("/")[0]
        fpath = (storage_dir() / "sentiment" / "funding.parquet" if base == "BTC"
                 else storage_dir() / "sentiment" / f"funding_{base}.parquet")
        if not fpath.exists():
            raise SystemExit(f"perp backtest needs {base} funding history: save "
                             f"sentiment.sources.fetch_funding_rate('{base}/USDT:USDT') to {fpath.name}")
        funding = pd.read_parquet(fpath)
        log.info("loaded %d funding-rate periods (perp mode, leverage cap %.1fx)", len(funding), max_leverage)

    cfg = BacktestConfig(
        initial_cash=sys_cfg["backtest"]["initial_cash"],
        fee_bps=fee_bps,
        slippage_bps=sys_cfg["slippage_bps"],
        risk=RiskConfig(**risk_kwargs),
        market=market,
        max_leverage=max_leverage,
    )

    split = int(len(bars) * (1 - args.oos))
    segments = {"In-sample": bars.iloc[:split]}
    if args.oos > 0:
        segments["Out-of-sample"] = bars.iloc[split:].reset_index(drop=True)

    results = {}
    for label, segment in segments.items():
        # A fresh strategy instance per segment, so no state leaks across segments.
        result = run_backtest(build_strategy(bot_cfg), segment, cfg,
                              sentiment=sentiment, funding=funding)
        results[label] = result
        m = result.metrics
        log.info(
            "%s: return %.2f%% | sharpe %.2f | max DD %.2f%% | fills %d | buy&hold %.2f%%",
            label, m["total_return"] * 100, m["sharpe"],
            m["max_drawdown"] * 100, m["n_fills"], m["buy_hold_return"] * 100,
        )

    report = render_backtest_report(results, f"Backtest report - {bot_cfg['name']}")
    path = save_report(report, f"backtest_{bot_cfg['name']}")
    log.info("report saved: %s", path)
    print("\n" + report)


if __name__ == "__main__":
    main()
