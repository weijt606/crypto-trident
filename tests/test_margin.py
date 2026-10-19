"""Tests for margin accounting, short/leverage risk checks and the perp backtest."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.engine import BacktestConfig, run_backtest
from core.models import AccountView, Bar, Order, Side, Signal
from strategies.base import Strategy
from safety.guard import RiskConfig, RiskGuard
from execution.margin_portfolio import MarginPortfolio


def mk_order(side: Side, qty: float, ts: int = 0) -> Order:
    return Order(ts, "BTC/USDT:USDT", side, qty, "test-bot")


def mk_portfolio(cash=1000.0, lev=3.0) -> MarginPortfolio:
    return MarginPortfolio(cash, fee_bps=0.0, slippage_bps=0.0, max_leverage=lev)


# ---- MarginPortfolio ----

def test_short_pnl_is_symmetric():
    p = mk_portfolio()
    p.execute(mk_order(Side.SELL, 1.0), 100.0)   # open short 1 @100
    assert p.position_qty == -1.0
    assert p.equity(90.0) == pytest.approx(1010.0)  # down 10 -> +10
    p.execute(mk_order(Side.BUY, 1.0), 90.0)     # close short
    assert p.position_qty == 0 and p.cash == pytest.approx(1010.0)


@pytest.mark.parametrize("open_side,close_side", [(Side.SELL, Side.BUY), (Side.BUY, Side.SELL)])
def test_round_trip_at_same_price_costs_two_fees(open_side, close_side):
    """Entry fees must reduce equity for shorts as well as longs (regression:
    short entry fees were folded into avg_cost with the long sign and showed as profit)."""
    p = MarginPortfolio(1000.0, fee_bps=10.0, slippage_bps=0.0, max_leverage=3.0)
    fee = 2.0 * 100.0 * 10 / 10_000   # one side: notional 200 x 10 bps
    p.execute(mk_order(open_side, 2.0), 100.0)
    assert p.equity(100.0) == pytest.approx(1000.0 - fee)   # entry fee already reflected
    p.execute(mk_order(close_side, 2.0), 100.0)
    assert p.position_qty == 0
    assert p.cash == pytest.approx(1000.0 - 2 * fee)
    assert p.equity(100.0) == pytest.approx(1000.0 - 2 * fee)
    assert p.realized_pnl == pytest.approx(-2 * fee)
    assert p.fees_paid == pytest.approx(2 * fee)


def test_short_add_and_partial_close_keep_fees_as_costs():
    p = MarginPortfolio(1000.0, fee_bps=10.0, slippage_bps=0.0, max_leverage=3.0)
    p.execute(mk_order(Side.SELL, 1.0), 100.0)
    p.execute(mk_order(Side.SELL, 1.0), 100.0)
    assert p.equity(100.0) == pytest.approx(1000.0 - 0.2)
    p.execute(mk_order(Side.BUY, 1.0), 100.0)
    assert p.equity(100.0) == pytest.approx(1000.0 - 0.3)
    p.execute(mk_order(Side.BUY, 1.0), 100.0)
    assert p.cash == pytest.approx(1000.0 - 0.4)


def test_flip_closes_then_opens_at_new_avg():
    p = mk_portfolio()
    p.execute(mk_order(Side.BUY, 1.0), 100.0)
    p.execute(mk_order(Side.SELL, 3.0), 120.0)   # close 1 (+20) + open short 2 @120
    assert p.position_qty == pytest.approx(-2.0)
    assert p.avg_cost == pytest.approx(120.0)
    assert p.realized_pnl == pytest.approx(20.0)


def test_leverage_clamp_shrinks_qty_to_cap():
    p = mk_portfolio(cash=100.0, lev=3.0)
    fill = p.execute(mk_order(Side.BUY, 10.0), 100.0)  # wants notional 1000 > 300
    assert fill.qty == pytest.approx(3.0)               # shrunk to 3x
    assert abs(p.position_qty) * 100.0 <= 100.0 * 3.0 * (1 + 1e-9)


def test_funding_longs_pay_positive_shorts_receive():
    p = mk_portfolio()
    p.execute(mk_order(Side.BUY, 1.0), 100.0)
    pay = p.apply_funding(0.0001, 100.0)
    assert pay == pytest.approx(0.01) and p.cash == pytest.approx(999.99)
    p2 = mk_portfolio()
    p2.execute(mk_order(Side.SELL, 1.0), 100.0)
    assert p2.apply_funding(0.0001, 100.0) == pytest.approx(-0.01)


def test_liquidation_when_equity_below_maintenance():
    p = mk_portfolio(cash=100.0, lev=3.0)
    p.execute(mk_order(Side.BUY, 3.0), 100.0)        # full 3x leverage
    assert not p.check_liquidation(80.0)             # equity=40 > 240*0.5%
    assert p.check_liquidation(67.0)                 # equity=1 <= 201*0.5% ~ 1.005 -> liquidated
    assert p.liquidated and p.position_qty == 0
    assert p.execute(mk_order(Side.BUY, 1.0), 100.0) is None  # no trading after liquidation


# ---- RiskGuard short/leverage ----

def test_default_config_forbids_shorting_like_spot():
    g = RiskGuard(RiskConfig(), 1000.0)
    v = g.check_order(mk_order(Side.SELL, 1.0), 100.0, 1000.0, 0.0)
    assert not v.ok and "short selling" in v.reason


def test_allow_short_permits_short_within_leverage_cap():
    cfg = RiskConfig(allow_short=True, max_leverage=3.0, max_order_notional=10_000)
    g = RiskGuard(cfg, 1000.0)
    assert g.check_order(mk_order(Side.SELL, 2.0), 100.0, 1000.0, 0.0).ok   # notional 200 < 2850
    v = g.check_order(mk_order(Side.SELL, 30.0), 100.0, 1000.0, 0.0)        # notional 3000 > 2850
    assert not v.ok and "exceeds limit" in v.reason


def test_reducing_always_allowed():
    cfg = RiskConfig(allow_short=True, max_leverage=3.0)
    g = RiskGuard(cfg, 1000.0)
    # short 30 (over the cap); buying back 5 reduces exposure -> allowed
    assert g.check_order(mk_order(Side.BUY, 5.0), 100.0, 1000.0, -30.0).ok


# ---- perp backtest engine ----

class FlipStrategy(Strategy):
    """Scripted strategy: open short on bar 2, close it on bar 5."""

    def __init__(self):
        super().__init__("flip-bot", "BTC/USDT", "1h", {})
        self._i = 0

    def on_bar(self, bar: Bar, account: AccountView) -> list[Signal]:
        self._i += 1
        if self._i == 2:
            return [Signal(Side.SELL, account.equity * 0.9 / bar.close, reason="open short")]
        if self._i == 5 and account.position_qty < 0:
            return [Signal(Side.BUY, -account.position_qty, reason="close short")]
        return []


def _bars(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame([
        {"ts": i * 3_600_000, "open": p, "high": p * 1.001,
         "low": p * 0.999, "close": p, "volume": 1.0}
        for i, p in enumerate(prices)
    ])


def test_perp_backtest_short_profits_in_downtrend_with_funding():
    bars = _bars([100, 100, 98, 96, 94, 94, 94])
    funding = pd.DataFrame({"ts": [2 * 3_600_000], "value": [-0.0001]})  # shorts pay negative rates
    cfg = BacktestConfig(initial_cash=1000, fee_bps=5, slippage_bps=0,
                         market="perp", max_leverage=3,
                         risk=RiskConfig(allow_short=True, max_leverage=3,
                                         max_order_notional=10_000))
    r = run_backtest(FlipStrategy(), bars, cfg, funding=funding)
    assert r.metrics["total_return"] > 0          # short profits in a downtrend
    assert len(r.fills) == 2


def test_perp_backtest_without_funding_refuses_to_run():
    cfg = BacktestConfig(market="perp")
    with pytest.raises(ValueError, match="funding rate"):
        run_backtest(FlipStrategy(), _bars([100, 100, 100]), cfg, funding=None)
