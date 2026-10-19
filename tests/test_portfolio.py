"""Core accounting tests: every cent must reconcile."""
import pytest

from conftest import T0
from core.models import Order, Side
from execution.portfolio import Portfolio


def order(side, qty):
    return Order(ts=T0, symbol="BTC/USDT", side=side, qty=qty, bot="test")


def test_buy_accounting_with_fee_and_slippage():
    p = Portfolio(cash=1000, fee_bps=10, slippage_bps=5)
    fill = p.execute(order(Side.BUY, 1.0), ref_price=100)
    assert fill.price == pytest.approx(100.05)          # +5bps slippage
    assert fill.fee == pytest.approx(100.05 * 0.001)    # 10bps fee
    assert p.cash == pytest.approx(1000 - 100.05 - fill.fee)
    assert p.position_qty == 1.0
    assert p.avg_cost == pytest.approx(100.05 + fill.fee)  # fee folded into cost basis


def test_sell_realized_pnl():
    p = Portfolio(cash=1000, fee_bps=0, slippage_bps=0)
    p.execute(order(Side.BUY, 1.0), ref_price=100)
    fill = p.execute(order(Side.SELL, 1.0), ref_price=110)
    assert fill.realized_pnl == pytest.approx(10.0)
    assert p.cash == pytest.approx(1010.0)
    assert p.position_qty == 0.0
    assert p.avg_cost == 0.0


def test_buy_clipped_to_cash():
    p = Portfolio(cash=50, fee_bps=10, slippage_bps=0)
    fill = p.execute(order(Side.BUY, 10.0), ref_price=100)  # wants 1000 worth
    assert fill.qty < 10.0
    assert p.cash == pytest.approx(0.0, abs=1e-9)
    assert p.cash >= -1e-9  # never overdraws


def test_sell_clipped_to_position():
    p = Portfolio(cash=0, fee_bps=0, slippage_bps=0)
    p.cash = 100
    p.execute(order(Side.BUY, 1.0), ref_price=100)
    fill = p.execute(order(Side.SELL, 5.0), ref_price=100)
    assert fill.qty == pytest.approx(1.0)
    assert p.position_qty == 0.0


def test_sell_with_nothing_returns_none():
    p = Portfolio(cash=100, fee_bps=0, slippage_bps=0)
    assert p.execute(order(Side.SELL, 1.0), ref_price=100) is None


def test_equity_consistency():
    p = Portfolio(cash=1000, fee_bps=10, slippage_bps=5)
    p.execute(order(Side.BUY, 2.0), ref_price=100)
    # equity = cash + position value
    assert p.equity(100) == pytest.approx(p.cash + 2.0 * 100)


def test_restart_restores_avg_cost_from_fills():
    """Restart regression: the equity snapshot has no avg_cost, so it must be
    rebuilt from fills after a restart; otherwise the next SELL books realized
    PnL against a zero cost basis (phantom profit)."""
    import pandas as pd

    # continuous run: buy 1@100, buy 1@60 (avg=80), sell 1@90 -> realized=(90-80)*1=10
    cont = Portfolio(cash=1000, fee_bps=0, slippage_bps=0)
    cont.execute(order(Side.BUY, 1.0), ref_price=100)
    cont.execute(order(Side.BUY, 1.0), ref_price=60)
    cont_fill = cont.execute(order(Side.SELL, 1.0), ref_price=90)
    assert cont_fill.realized_pnl == pytest.approx(10.0)

    # simulated restart: only cash/position_qty restored (avg_cost lost, stays 0)
    fills = pd.DataFrame([{"side": "buy", "qty": 1.0, "price": 100.0, "fee": 0.0}])
    restarted = Portfolio(cash=1000, fee_bps=0, slippage_bps=0)
    restarted.cash = 900.0          # cash after buy 1@100
    restarted.position_qty = 1.0
    # without rebuild: avg_cost stays 0, buy 1@60 drags avg to 30 (should be 80),
    # sell 1@90 books realized=60 (should be 10) -- phantom profit
    bug = Portfolio(cash=900, fee_bps=0, slippage_bps=0)
    bug.position_qty = 1.0
    bug.execute(order(Side.BUY, 1.0), ref_price=60)
    assert bug.execute(order(Side.SELL, 1.0), ref_price=90).realized_pnl == pytest.approx(60.0)

    # after rebuild, identical to the continuous run
    restarted.restore_avg_cost(fills)
    assert restarted.avg_cost == pytest.approx(100.0)
    restarted.execute(order(Side.BUY, 1.0), ref_price=60)
    assert restarted.avg_cost == pytest.approx(80.0)
    fixed_fill = restarted.execute(order(Side.SELL, 1.0), ref_price=90)
    assert fixed_fill.realized_pnl == pytest.approx(10.0)
