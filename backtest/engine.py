"""Event-driven backtest engine.

Honesty rules:
- a signal produced at the close of bar t fills at the open of bar t+1
  (plus slippage), so there is no lookahead
- fees and slippage are always charged; accounting shares Portfolio with paper trading
- the RiskGuard is active throughout: an order rejected in backtest would be
  rejected live as well
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from backtest.metrics import compute_metrics
from core.models import Bar, Fill, Order, Side
from strategies.base import Strategy
from safety.guard import RiskConfig, RiskGuard
from execution.margin_portfolio import MarginPortfolio
from execution.portfolio import Portfolio


@dataclass
class BacktestConfig:
    initial_cash: float = 10_000.0
    fee_bps: float = 10.0       # 0.1% taker (pass 5 = 0.05% for perp)
    slippage_bps: float = 5.0
    risk: RiskConfig = field(default_factory=RiskConfig)
    market: str = "spot"        # spot / perp
    max_leverage: float = 1.0   # perp accounting leverage clamp (risk guard has its own limit)


@dataclass
class BacktestResult:
    bot: str
    symbol: str
    timeframe: str
    equity_curve: pd.DataFrame
    fills: list[Fill]
    rejections: list[tuple[int, str]]
    risk_events: list[tuple[int, str]]
    metrics: dict


def run_backtest(
    bot: Strategy,
    bars: pd.DataFrame,
    cfg: BacktestConfig,
    sentiment: pd.DataFrame | None = None,
    funding: pd.DataFrame | None = None,
) -> BacktestResult:
    """bars: DataFrame[ts, open, high, low, close, volume], ascending.

    sentiment: optional composite sentiment table (avail_ts/score). Injected
    into bot.sentiment when avail_ts <= bar.ts, matching exactly what a live
    bot would know, so there is no lookahead.
    funding: required for perp, DataFrame[ts, value] (8h funding rates). Longs
    pay positive rates; a perp backtest without funding refuses to run. Optional
    for spot: only injected into bot.funding as a signal (settled values with
    ts <= bar.ts), no cost is charged.
    """
    if cfg.market == "perp":
        if funding is None or not len(funding):
            raise ValueError("perp backtest requires a funding rate series (backtest honesty)")
        portfolio = MarginPortfolio(cfg.initial_cash, cfg.fee_bps,
                                    cfg.slippage_bps, cfg.max_leverage)
    else:
        portfolio = Portfolio(cfg.initial_cash, cfg.fee_bps, cfg.slippage_bps)
    guard = RiskGuard(cfg.risk, cfg.initial_cash)

    fund_rows = (funding.sort_values("ts").reset_index(drop=True)
                 if funding is not None and len(funding) else None)
    fund_idx = 0
    if fund_rows is not None:  # skip settlements before the backtest start
        first_ts = int(bars["ts"].iloc[0])
        while fund_idx < len(fund_rows) and fund_rows["ts"].iloc[fund_idx] <= first_ts:
            fund_idx += 1

    sent_rows = (
        sentiment.sort_values("avail_ts").reset_index(drop=True)
        if sentiment is not None and len(sentiment)
        else None
    )
    sent_idx = 0

    fills: list[Fill] = []
    rejections: list[tuple[int, str]] = []
    risk_events: list[tuple[int, str]] = []
    curve: list[dict] = []
    pending: list[Order] = []

    for row in bars.itertuples(index=False):
        bar = Bar(
            ts=int(row.ts), open=float(row.open), high=float(row.high),
            low=float(row.low), close=float(row.close), volume=float(row.volume),
            symbol=bot.symbol, timeframe=bot.timeframe,
        )

        # 1) orders produced at the previous close fill at this bar's open
        for order in pending:
            fill = portfolio.execute(order, bar.open)
            if fill is not None:
                fills.append(fill)
        pending.clear()

        # 1.5) funding: settle rates due within this bar (charged for perp,
        #      signal-only for spot) and inject bot.funding; for perp also check
        #      liquidation at the bar's most adverse price
        if fund_rows is not None:
            settled = 0.0
            n_settled = 0
            while fund_idx < len(fund_rows) and fund_rows["ts"].iloc[fund_idx] <= bar.ts:
                rate = float(fund_rows["value"].iloc[fund_idx])
                if cfg.market == "perp":
                    portfolio.apply_funding(rate, bar.open)
                settled += rate
                n_settled += 1
                fund_idx += 1
            if n_settled:
                bot.funding = settled
            if cfg.market == "perp":
                adverse = bar.low if portfolio.position_qty > 0 else bar.high
                if portfolio.check_liquidation(adverse):
                    risk_events.append((bar.ts, f"LIQUIDATION: liquidated at {adverse:.2f}"))
                    curve.append({"ts": bar.ts, "equity": portfolio.equity(bar.close),
                                  "close": bar.close})
                    break

        # 2) mark equity at the close and run risk checks
        equity = portfolio.equity(bar.close)
        curve.append({"ts": bar.ts, "equity": equity, "close": bar.close})
        for event in guard.on_mark(bar.ts, equity):
            risk_events.append((bar.ts, event))

        if guard.killed:
            # max-drawdown kill: flatten at the close (long or short) and stop
            if portfolio.position_qty != 0:
                side = Side.SELL if portfolio.position_qty > 0 else Side.BUY
                liq = Order(bar.ts, bot.symbol, side,
                            abs(portfolio.position_qty), bot.name, reason="kill switch liquidation")
                fill = portfolio.execute(liq, bar.close)
                if fill is not None:
                    fills.append(fill)
            curve[-1]["equity"] = portfolio.equity(bar.close)
            break

        # 3) inject the latest available sentiment (avail_ts <= bar.ts)
        if sent_rows is not None:
            while (
                sent_idx < len(sent_rows)
                and sent_rows["avail_ts"].iloc[sent_idx] <= bar.ts
            ):
                bot.sentiment = float(sent_rows["score"].iloc[sent_idx])
                sent_idx += 1

        # 4) strategy signals -> risk guard -> pending queue for the next bar
        account = portfolio.view(bar.close)
        for sig in bot.on_bar(bar, account):
            order = Order(bar.ts, bot.symbol, sig.side, sig.qty,
                          bot.name, reason=sig.reason)
            verdict = guard.check_order(order, bar.close, account.equity,
                                        portfolio.position_qty)
            if verdict.ok:
                pending.append(order)
            else:
                rejections.append((bar.ts, f"{sig.side.value} {sig.qty:.6f}: {verdict.reason}"))

    equity_curve = pd.DataFrame(curve)
    fills_df = pd.DataFrame([f.__dict__ for f in fills]) if fills else pd.DataFrame(
        columns=["ts", "symbol", "side", "qty", "price", "fee", "bot", "realized_pnl"]
    )
    if len(fills_df):
        fills_df["side"] = fills_df["side"].map(lambda s: s.value if isinstance(s, Side) else s)

    metrics = compute_metrics(equity_curve, fills_df, bot.timeframe, cfg.initial_cash)
    metrics["risk_events"] = len(risk_events)
    metrics["rejections"] = len(rejections)

    return BacktestResult(
        bot=bot.name, symbol=bot.symbol, timeframe=bot.timeframe,
        equity_curve=equity_curve, fills=fills, rejections=rejections,
        risk_events=risk_events, metrics=metrics,
    )
