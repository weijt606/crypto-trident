"""Paper trader.

Driven by real market data (polls the public REST API, no API key needed) and fills
against virtual cash. Shares Portfolio accounting and RiskGuard with the backtest
engine; the only differences are the data source and the fill reference price
(paper uses the latest closed bar's close + slippage).

Resumable: account state is persisted every bar and restored from SQLite on restart.
"""
from __future__ import annotations

import json
import time

from core.polling import next_sleep
from data.fetcher import fetch_latest_closed_bar, make_exchange
from monitor.heartbeat import beat
from execution.warmup import funding_path, warmup_strategy
from data.store import TradeLog
from sentiment.composite import as_of
from sentiment.store import load_composite
from core.models import Bar, Order, Side
from monitor.alerts import send_alert
from monitor.logger import get_logger
from strategies.base import Strategy
from safety.guard import RiskConfig, RiskGuard
from execution.halt import TraderHalted
from execution.margin_portfolio import MarginPortfolio
from execution.portfolio import Portfolio

log = get_logger("paper")


def _perp_avg_cost_from_snapshot(name: str, state: dict) -> float:
    """Recover a perp's avg_cost from its last equity snapshot.

    MarginPortfolio.equity() = cash + (last_price - avg_cost) * qty, and every
    snapshot stores equity, cash, qty and last_price, so avg_cost is determined.
    Refuses (raises) rather than resuming a non-flat position with avg_cost=0,
    which would silently corrupt equity and every risk check after restart."""
    qty, eq, cash, px = (state.get(k) for k in ("position_qty", "equity", "cash", "last_price"))
    try:
        avg = float(px) - (float(eq) - float(cash)) / float(qty)
    except (TypeError, ValueError, ZeroDivisionError):
        avg = float("nan")
    if not (avg == avg and avg > 0 and avg != float("inf")):
        raise RuntimeError(
            f"Cannot restore open perp position for {name}: avg_cost is not recoverable "
            f"from the last snapshot (qty={qty}). Flatten or reset the paper state manually."
        )
    return avg


class PaperTrader:
    def __init__(
        self,
        bot: Strategy,
        initial_cash: float,
        fee_bps: float,
        slippage_bps: float,
        risk: RiskConfig,
        exchange_id: str = "binance",
        trade_log: TradeLog | None = None,
        market: str = "spot",
        max_leverage: float = 1.0,
    ):
        self.bot = bot
        self.market = market
        self.exchange = make_exchange(exchange_id)
        self.trade_log = trade_log or TradeLog()
        # Perps use margin accounting (signed position + funding + liquidation),
        # the same implementation as the backtest engine.
        if market == "perp":
            self.portfolio = MarginPortfolio(initial_cash, fee_bps, slippage_bps, max_leverage)
        else:
            self.portfolio = Portfolio(initial_cash, fee_bps, slippage_bps)
        self.guard = RiskGuard(risk, initial_cash)
        self.last_bar_ts: int | None = None

        state = self.trade_log.latest_state(bot.name)
        if state:
            self.portfolio.cash = state["cash"]
            self.portfolio.position_qty = state["position_qty"]
            self.last_bar_ts = state["ts"]
            # The equity snapshot does not persist avg_cost directly. Spot rebuilds it
            # from fills (otherwise the next SELL would book phantom profit); perps
            # recover it exactly from the snapshot identity
            # equity = cash + (last_price - avg_cost) * qty.
            if market != "perp" and self.portfolio.position_qty > 0:
                self.portfolio.restore_avg_cost(self.trade_log.fills_df(bot.name))
            elif market == "perp" and self.portfolio.position_qty != 0:
                self.portfolio.avg_cost = _perp_avg_cost_from_snapshot(bot.name, state)
            log.info(
                "Restored %s: cash=%.2f qty=%.6f avg_cost=%.2f (last bar ts=%d). "
                "Strategy indicators will be rebuilt by warmup",
                bot.name, state["cash"], state["position_qty"],
                getattr(self.portfolio, "avg_cost", 0.0), state["ts"],
            )

    def step(self, raw_bar: list) -> None:
        """Process one newly closed bar, following exactly the backtest engine's flow."""
        bar = Bar(
            ts=int(raw_bar[0]), open=float(raw_bar[1]), high=float(raw_bar[2]),
            low=float(raw_bar[3]), close=float(raw_bar[4]), volume=float(raw_bar[5]),
            symbol=self.bot.symbol, timeframe=self.bot.timeframe,
        )
        name = self.bot.name

        equity = self.portfolio.equity(bar.close)
        for event in self.guard.on_mark(bar.ts, equity):
            log.warning("[risk] %s", event)
            self.trade_log.record_event(bar.ts, name, "risk", event)
            send_alert(f"{name}: {event}")

        if self.guard.killed:
            self._liquidate_all(bar, "kill switch liquidation")
            self._mark(bar)
            send_alert(f"{name} killed: {self.guard.kill_reason}; manual action required")
            raise TraderHalted(f"Kill switch halt: {self.guard.kill_reason}")

        # Inject the latest available sentiment (same no-lookahead rule as backtest).
        score = as_of(load_composite(), bar.ts)
        if score is not None:
            self.bot.sentiment = score

        # Inject the sum of funding rates settled since the previous bar (settled values,
        # no lookahead; fail-soft). Perps also settle that funding against margin.
        settled_funding = None
        try:
            import pandas as pd

            fund = pd.read_parquet(funding_path(self.bot.symbol))
            prev = self.last_bar_ts or 0
            window = fund[(fund["ts"] > prev) & (fund["ts"] <= bar.ts)]
            if len(window):
                settled_funding = float(window["value"].sum())
                self.bot.funding = settled_funding
        except Exception as e:
            log.warning("Funding injection failed (strategy sees no data): %s", e)

        if self.market == "perp":
            if settled_funding is not None and self.portfolio.position_qty != 0:
                self.portfolio.apply_funding(settled_funding, bar.close)
            # Check liquidation at the bar's worst price (low for longs, high for shorts).
            adverse = bar.low if self.portfolio.position_qty > 0 else bar.high
            if self.portfolio.check_liquidation(adverse):
                msg = f"LIQUIDATION at {adverse:.2f}"
                log.warning("[risk] %s", msg)
                self.trade_log.record_event(bar.ts, name, "risk", msg)
                send_alert(f"{name} perp liquidated: {msg}; halting")
                self._mark(bar)
                raise TraderHalted(f"Liquidation halt: {msg}")

        account = self.portfolio.view(bar.close)
        for sig in self.bot.on_bar(bar, account):
            order = Order(bar.ts, bar.symbol, sig.side, sig.qty, name, reason=sig.reason)
            verdict = self.guard.check_order(
                order, bar.close, account.equity, self.portfolio.position_qty
            )
            if not verdict.ok:
                log.warning("[reject] %s %s: %s", sig.side.value, sig.qty, verdict.reason)
                self.trade_log.record_event(bar.ts, name, "reject", verdict.reason)
                continue
            fill = self.portfolio.execute(order, bar.close)
            if fill:
                self.trade_log.record_fill(fill)
                log.info(
                    "[fill] %s %.6f @ %.2f fee %.4f (%s)",
                    fill.side.value, fill.qty, fill.price, fill.fee, sig.reason,
                )
        self._mark(bar)

    def _liquidate_all(self, bar: Bar, reason: str) -> None:
        """Close the whole position in either direction (spot is long-only; perps may be short)."""
        pos = self.portfolio.position_qty
        if pos == 0:
            return
        side = Side.SELL if pos > 0 else Side.BUY
        liq = Order(bar.ts, bar.symbol, side, abs(pos), self.bot.name, reason=reason)
        fill = self.portfolio.execute(liq, bar.close)
        if fill:
            self.trade_log.record_fill(fill)

    def _snapshot_strategy(self, ts: int) -> None:
        """Persist the strategy's describe() snapshot for the dashboard; never fatal."""
        try:
            self.trade_log.record_strategy_state(
                ts, self.bot.name,
                json.dumps(self.bot.describe(), ensure_ascii=False, default=str),
            )
        except Exception as e:
            log.warning("Strategy snapshot write failed: %s", e)

    def _mark(self, bar: Bar) -> None:
        self.trade_log.record_equity(
            bar.ts, self.bot.name, self.portfolio.equity(bar.close),
            self.portfolio.cash, self.portfolio.position_qty, bar.close,
        )
        self._snapshot_strategy(bar.ts)
        self.last_bar_ts = bar.ts

    def run_forever(self) -> None:
        log.info(
            "Paper trader started: %s %s %s, starting equity %.2f USDT",
            self.bot.name, self.bot.symbol, self.bot.timeframe,
            self.portfolio.equity(0) if self.portfolio.position_qty == 0
            else self.portfolio.cash,
        )
        # Warmup rebuilds strategy indicators so long-warmup strategies can trade right
        # after a restart. Align last_bar_ts to the replay end so replayed bars are not
        # traded as if they were new.
        warmed = warmup_strategy(self.bot)
        if warmed is not None:
            self.last_bar_ts = max(self.last_bar_ts or 0, warmed)
        # Write a snapshot immediately so the dashboard need not wait for the next bar.
        self._snapshot_strategy(self.last_bar_ts or int(time.time() * 1000))
        errors = 0
        while True:
            beat(self.bot.name)  # heartbeat each loop: proves the process is polling
            try:
                raw = fetch_latest_closed_bar(
                    self.exchange, self.bot.symbol, self.bot.timeframe
                )
                if raw and (self.last_bar_ts is None or raw[0] > self.last_bar_ts):
                    self.step(raw)
                errors = 0  # success: reset backoff
            except SystemExit:
                raise
            except Exception as e:  # network blips / rate limits: back off and retry
                errors += 1
                log.error("Polling error (%d in a row): %s", errors, e)
            time.sleep(next_sleep(self.bot.timeframe, errors))
