"""Performance metrics, computed identically for backtest and paper trading."""
from __future__ import annotations

import math

import pandas as pd

from core.models import SECONDS_PER_YEAR, TIMEFRAME_SECONDS

MIN_CAGR_DAYS = 30  # annualizing a shorter span is meaningless; CAGR is reported as None


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = 1 - equity / peak
    return float(dd.max()) if len(dd) else 0.0


def sharpe(equity: pd.Series, timeframe: str) -> float:
    """Annualized Sharpe ratio from per-bar returns (risk-free rate = 0)."""
    rets = equity.pct_change().dropna()
    if len(rets) < 2 or rets.std() == 0:
        return 0.0
    bars_per_year = SECONDS_PER_YEAR / TIMEFRAME_SECONDS[timeframe]
    return float(rets.mean() / rets.std() * math.sqrt(bars_per_year))


def compute_metrics(
    equity_curve: pd.DataFrame,  # columns: ts, equity, close
    fills_df: pd.DataFrame,      # columns: side, fee, realized_pnl, ... (may be empty)
    timeframe: str,
    initial_cash: float,
) -> dict:
    if equity_curve.empty:
        return {"error": "no equity data"}

    eq = equity_curve["equity"]
    final_equity = float(eq.iloc[-1])
    total_return = final_equity / initial_cash - 1

    span_years = (
        (equity_curve["ts"].iloc[-1] - equity_curve["ts"].iloc[0])
        / 1000 / SECONDS_PER_YEAR
    )
    cagr = None
    if span_years * 365 >= MIN_CAGR_DAYS and final_equity > 0:
        cagr = (final_equity / initial_cash) ** (1 / span_years) - 1

    sells = fills_df[fills_df["side"] == "sell"] if len(fills_df) else fills_df

    # Defensive check for a lost cost basis: if avg_cost were missing, most of
    # the sale proceeds would be booked as realized_pnl. Flag a sell when
    # realized / proceeds exceeds a plausible single round-trip gain for the
    # timeframe: 8% for intraday bars, 50% for daily and above. False positives
    # are cheap: flagged fills are only excluded from the "clean" figures, the
    # raw ledger figure is always kept.
    ratio = 0.5 if timeframe in ("1d", "3d", "1w") else 0.08
    has_px = len(sells) and {"qty", "price"} <= set(sells.columns)
    tainted = sells[sells["realized_pnl"] >= sells["qty"] * sells["price"] * ratio] \
        if has_px else sells.iloc[0:0]
    clean_sells = sells.drop(tainted.index) if len(tainted) else sells
    win_rate = float((clean_sells["realized_pnl"] > 0).mean()) if len(clean_sells) else float("nan")
    realized_total = round(float(fills_df["realized_pnl"].sum()), 2) if len(fills_df) else 0.0

    buy_hold = float(equity_curve["close"].iloc[-1] / equity_curve["close"].iloc[0] - 1)

    extra = {}
    if len(tainted):
        extra = {
            "realized_pnl_tainted": round(float(tainted["realized_pnl"].sum()), 2),
            "realized_pnl_clean": round(realized_total - float(tainted["realized_pnl"].sum()), 2),
            "n_tainted_fills": int(len(tainted)),
        }

    return {
        **extra,
        "initial_cash": initial_cash,
        "final_equity": round(final_equity, 2),
        "total_return": round(total_return, 4),
        "cagr": round(cagr, 4) if cagr is not None else None,
        "sharpe": round(sharpe(eq, timeframe), 2),
        "max_drawdown": round(max_drawdown(eq), 4),
        "n_fills": int(len(fills_df)),
        "n_round_trips": int(len(sells)),
        "win_rate": round(win_rate, 4) if not math.isnan(win_rate) else None,
        "total_fees": round(float(fills_df["fee"].sum()), 2) if len(fills_df) else 0.0,
        "realized_pnl": realized_total,
        "buy_hold_return": round(buy_hold, 4),
        "bars": int(len(equity_curve)),
        "span_days": round(span_years * 365, 1),
    }
