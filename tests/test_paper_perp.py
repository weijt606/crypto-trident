"""Paper perp accounting tests: shorting, leverage cap, kill-switch symmetric liquidation."""
from __future__ import annotations

import pandas as pd
import pytest

from core.models import Side, Signal
from data.store import TradeLog
from strategies.base import Strategy
from safety.guard import RiskConfig
from execution.margin_portfolio import MarginPortfolio
from execution.paper import PaperTrader


class ScriptBot(Strategy):
    def __init__(self):
        super().__init__("perp-t", "BTC/USDT", "1h", {})
        self.queue: list = []

    def on_bar(self, bar, account):
        return self.queue.pop(0) if self.queue else []


def raw(ts_h, price):
    return [ts_h * 3_600_000, price, price + 1, price - 1, price, 1.0]


RISK = dict(allow_short=True, max_leverage=3, max_order_notional=100_000,
            allowed_symbols=["BTC/USDT"])


@pytest.fixture()
def perp(tmp_path, monkeypatch):
    monkeypatch.setattr("execution.paper.load_composite", lambda: None)
    monkeypatch.setattr("execution.paper.as_of", lambda *a, **k: None)
    monkeypatch.setattr(pd, "read_parquet",
                        lambda *a, **k: pd.DataFrame(columns=["ts", "value"]))
    monkeypatch.setattr("execution.paper.make_exchange", lambda *a, **k: None)
    monkeypatch.setattr("execution.paper.send_alert", lambda *a, **k: None)
    bot = ScriptBot()
    t = PaperTrader(bot, 1000.0, 5.0, 0.0, RiskConfig(**RISK),
                    trade_log=TradeLog(tmp_path / "t.sqlite"),
                    market="perp", max_leverage=3)
    return t, bot


def test_uses_margin_portfolio(perp):
    t, _ = perp
    assert isinstance(t.portfolio, MarginPortfolio)


def test_short_signed_position(perp):
    t, bot = perp
    bot.queue = [[Signal(Side.SELL, 2.0)]]
    t.step(raw(1, 100))
    assert t.portfolio.position_qty == pytest.approx(-2.0)


def test_short_profits_on_drop(perp):
    t, bot = perp
    bot.queue = [[Signal(Side.SELL, 2.0)], []]
    t.step(raw(1, 100))
    before = t.portfolio.equity(100)
    t.step(raw(2, 90))            # down 10: short gains
    assert t.portfolio.equity(90) > before


def test_leverage_cap_rejects(perp):
    t, bot = perp
    bot.queue = [[Signal(Side.SELL, 100.0)]]   # notional 10000 >> 3 x 1000
    t.step(raw(1, 100))
    df = t.trade_log.events_df()
    assert (df["kind"] == "reject").any()


def test_kill_closes_short_symmetrically(perp):
    t, bot = perp
    bot.queue = [[Signal(Side.SELL, 28.0)]]     # short near the 3x leverage cap
    t.step(raw(1, 100))
    assert t.portfolio.position_qty < 0
    bot.queue = [[]]
    # price spike -> short loss triggers drawdown kill; must buy back and halt
    with pytest.raises(SystemExit):
        t.step(raw(2, 130))
    assert t.portfolio.position_qty == 0


def _restart(t, tmp_path):
    return PaperTrader(t.bot, 1000.0, 5.0, 0.0, RiskConfig(**RISK),
                       trade_log=TradeLog(tmp_path / "t.sqlite"),
                       market="perp", max_leverage=3)


@pytest.mark.parametrize("side,qty", [(Side.SELL, 2.0), (Side.BUY, 1.5)])
def test_restart_restores_perp_avg_cost(perp, tmp_path, side, qty):
    t, bot = perp
    bot.queue = [[Signal(side, qty)]]
    t.step(raw(1, 100))
    t.step(raw(2, 90))
    before_avg = t.portfolio.avg_cost
    before_eq = t.portfolio.equity(95.0)
    t2 = _restart(t, tmp_path)
    assert t2.portfolio.position_qty == pytest.approx(t.portfolio.position_qty)
    assert t2.portfolio.avg_cost == pytest.approx(before_avg)
    assert t2.portfolio.equity(95.0) == pytest.approx(before_eq)


def test_restart_refuses_unrecoverable_perp_position(perp, tmp_path):
    t, _ = perp
    # Corrupt snapshot: open position but equity/last_price missing.
    t.trade_log.conn.execute(
        "INSERT INTO equity VALUES (?,?,?,?,?,?)",
        (99 * 3_600_000, "perp-t", None, 1000.0, -1.0, None),
    )
    t.trade_log.conn.commit()
    with pytest.raises(RuntimeError, match="Cannot restore open perp position"):
        _restart(t, tmp_path)
