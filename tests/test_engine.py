"""Backtest engine tests: fill timing, accounting consistency, kill behavior."""
import pytest

from backtest.engine import BacktestConfig, run_backtest
from conftest import make_bars
from core.models import AccountView, Bar, Side, Signal
from strategies.base import Strategy
from strategies.sma_cross import SmaCross
from safety.guard import RiskConfig


class AllInOnce(Strategy):
    """Test helper: buy almost all-in on the first bar, then hold."""

    def __init__(self):
        super().__init__("all-in", "BTC/USDT", "1h", {})
        self.bought = False

    def on_bar(self, bar: Bar, account: AccountView) -> list[Signal]:
        if not self.bought:
            self.bought = True
            return [Signal(Side.BUY, account.equity * 0.94 / bar.close, "test entry")]
        return []


def cfg(**risk_overrides) -> BacktestConfig:
    return BacktestConfig(initial_cash=1000, fee_bps=10, slippage_bps=5,
                          risk=RiskConfig(**risk_overrides))


def test_fill_happens_at_next_bar_open():
    bars = make_bars([100, 102, 104, 106])
    result = run_backtest(AllInOnce(), bars, cfg())
    assert len(result.fills) == 1
    fill = result.fills[0]
    # signal at bar 0 close (100), fill at bar 1 open (=100) + 5bps slippage
    assert fill.price == pytest.approx(100 * 1.0005)


def test_equity_curve_consistent_and_no_overdraft():
    bars = make_bars([100, 102, 101, 105, 103, 108])
    result = run_backtest(AllInOnce(), bars, cfg())
    assert len(result.equity_curve) == len(bars)
    assert (result.equity_curve["equity"] > 0).all()
    # equity should track price once in a position
    final = result.equity_curve["equity"].iloc[-1]
    assert final > 1000  # price up 8%: still profitable after fees


def test_drawdown_kill_liquidates_and_stops():
    # 30% crash after entry -> drawdown kill -> flatten and stop
    closes = [100, 100, 95, 88, 80, 70, 65, 60]
    bars = make_bars(closes)
    result = run_backtest(AllInOnce(), bars, cfg(max_drawdown_pct=0.15))

    assert any("KILL" in e for _, e in result.risk_events)
    # engine stops early after the kill
    assert len(result.equity_curve) < len(bars)
    # last fill is the liquidating sell
    assert result.fills[-1].side == Side.SELL
    # loss is locked near the kill threshold instead of riding to -40%
    final = result.equity_curve["equity"].iloc[-1]
    assert final > 1000 * (1 - 0.30)


def test_sma_full_cycle_on_synthetic_trend():
    # long rise then long fall: expect one full buy/sell cycle
    closes = [100] * 10 + [100 + 3 * i for i in range(1, 21)] + \
             [160 - 4 * i for i in range(1, 21)]
    bars = make_bars([float(c) for c in closes])
    bot = SmaCross("sma-test", "BTC/USDT", "1h", {"fast": 3, "slow": 8, "alloc": 0.9})
    result = run_backtest(bot, bars, cfg())

    sides = [f.side for f in result.fills]
    assert Side.BUY in sides and Side.SELL in sides
    assert result.metrics["n_fills"] == len(result.fills)
    assert result.metrics["total_fees"] > 0


def test_risk_rejection_is_recorded():
    bars = make_bars([100, 102, 104])
    result = run_backtest(AllInOnce(), bars, cfg(max_order_notional=50))
    assert len(result.fills) == 0
    assert len(result.rejections) == 1
    assert "exceeds limit" in result.rejections[0][1]
