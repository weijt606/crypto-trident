"""Performance report from the paper ledger (default) or the live ledger (--live).

Usage:
    uv run python scripts/report.py --bot sma-demo
    uv run python scripts/report.py --bot sma-demo --live   # reads storage/live/live.sqlite
"""
import _bootstrap  # noqa: F401
import argparse

from backtest.metrics import compute_metrics
from core.config import load_bot_config, storage_dir
from data.store import TradeLog
from monitor.logger import get_logger
from monitor.report import render_metrics_table, save_report

log = get_logger("report")


def main() -> None:
    p = argparse.ArgumentParser(description="Render a performance report for a bot.")
    p.add_argument("--bot", required=True, help="config name under config/bots/")
    p.add_argument("--live", action="store_true", help="read the live ledger instead of the paper ledger")
    args = p.parse_args()

    bot_cfg = load_bot_config(args.bot)
    name = bot_cfg["name"]
    db = storage_dir() / ("live/live.sqlite" if args.live else "trades.sqlite")
    tlog = TradeLog(db, read_only=True)   # reports never create or modify a ledger
    kind = "Live" if args.live else "Paper"

    equity = tlog.equity_df(name)
    if equity.empty:
        log.info("no %s data for %s yet", kind.lower(), name)
        return
    fills = tlog.fills_df(name)
    events = tlog.events_df(name)

    curve = equity.rename(columns={"last_price": "close"})[["ts", "equity", "close"]]
    initial = float(equity["equity"].iloc[0])
    metrics = compute_metrics(curve, fills, bot_cfg["timeframe"], initial)
    metrics["risk_events"] = int((events["kind"] == "risk").sum()) if len(events) else 0
    metrics["rejections"] = int((events["kind"] == "reject").sum()) if len(events) else 0

    content = render_metrics_table(metrics, f"{kind} report - {name} ({bot_cfg['symbol']})")
    path = save_report(content, f"{'live' if args.live else 'paper'}_{name}")
    log.info("report saved: %s", path)
    print("\n" + content)


if __name__ == "__main__":
    main()
