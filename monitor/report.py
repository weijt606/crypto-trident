"""Performance report rendering (markdown), shared by backtest and paper reports."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.config import storage_dir

_METRIC_LABELS = {
    "initial_cash": "Initial cash (USDT)",
    "final_equity": "Final equity (USDT)",
    "total_return": "Total return",
    "cagr": "CAGR",
    "sharpe": "Sharpe ratio",
    "max_drawdown": "Max drawdown",
    "n_fills": "Fills",
    "n_round_trips": "Round trips",
    "win_rate": "Win rate",
    "total_fees": "Total fees (USDT)",
    "realized_pnl": "Realized PnL, raw ledger (USDT)",
    # The next three only appear when the defensive lost-cost-basis check flags fills.
    "realized_pnl_clean": "Realized PnL, excluding flagged fills (USDT)",
    "realized_pnl_tainted": "Of which from fills with a suspect cost basis (USDT)",
    "n_tainted_fills": "Fills flagged for a suspect cost basis",
    "buy_hold_return": "Buy & hold return",
    "bars": "Bars",
    "span_days": "Span (days)",
    "risk_events": "Risk events",
    "rejections": "Risk rejections",
}

_PCT_KEYS = {"total_return", "cagr", "max_drawdown", "win_rate", "buy_hold_return"}


def render_metrics_table(metrics: dict, title: str) -> str:
    lines = [f"### {title}", "", "| Metric | Value |", "|---|---|"]
    for key, label in _METRIC_LABELS.items():
        if key not in metrics:
            continue
        v = metrics[key]
        if v is None:
            v = "n/a"
        elif key in _PCT_KEYS:
            v = f"{v:.2%}"
        lines.append(f"| {label} | {v} |")
    return "\n".join(lines) + "\n"


def render_backtest_report(results: dict[str, "object"], header: str) -> str:
    """results: {segment label: BacktestResult}, e.g. {"In-sample": ..., "Out-of-sample": ...}"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [f"# {header}", f"\nGenerated: {now}\n"]
    for label, res in results.items():
        parts.append(render_metrics_table(res.metrics, f"{label} — {res.bot} ({res.symbol} {res.timeframe})"))
        if res.risk_events:
            parts.append("**Risk events:**\n")
            for ts, event in res.risk_events:
                t = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                parts.append(f"- {t} {event}")
            parts.append("")
    return "\n".join(parts)


def save_report(content: str, name: str) -> Path:
    d = storage_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = d / f"{stamp}_{name}.md"
    path.write_text(content)
    return path
