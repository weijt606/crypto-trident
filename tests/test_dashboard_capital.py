"""Dashboard returns must not count deposits or withdrawals as performance.

Covers the equity curve, the time-weighted return, daily PnL, the bot card's
absolute PnL and the strategy sparkline, which all share one capital mapping.
"""
from urllib.parse import parse_qs

import pandas as pd
import pytest

from data.store import TradeLog
from monitor import dashboard

DAY = 86_400_000
T0 = 1_785_000_000_000


def _day(n):
    return pd.Timestamp(T0 + n * DAY, unit="ms", tz="UTC").strftime("%Y-%m-%d")


@pytest.fixture
def wire(tmp_path, monkeypatch):
    def _wire(rows, cap=None, ledger="live"):
        tl = TradeLog(tmp_path / "ledger.sqlite")
        for n, eq in rows:
            tl.record_equity(T0 + n * DAY, "b1", eq, eq, 0.0, 0.0)
        tl.close()
        log = TradeLog(tmp_path / "ledger.sqlite")
        monkeypatch.setattr(dashboard, "_open_logs", lambda: (log, log))
        monkeypatch.setattr(dashboard, "_close", lambda *a: None)
        monkeypatch.setattr(dashboard, "_log_for", lambda *a: log)
        monkeypatch.setattr(dashboard, "_stage_of", lambda n: "live")
        monkeypatch.setattr(dashboard, "_capital_events",
                            lambda: {(d, "b1", ledger): v for d, v in (cap or {}).items()})
        bars = pd.DataFrame({"ts": [T0 - DAY], "close": [1.0]})
        monkeypatch.setattr(dashboard, "load_bars", lambda *a, **k: bars)
        return log
    return _wire


def test_equity_curve_excludes_deposit(wire):
    wire([(0, 100.0), (1, 110.0), (2, 160.0), (3, 176.0)], cap={_day(2): 50.0})
    curve = [p["equity"] for p in dashboard.api_equity(parse_qs("bot=b1"))["series"]]
    assert curve == pytest.approx([100.0, 110.0, 110.0, 121.0])


def test_deposit_on_day_without_snapshot_is_not_dropped(wire):
    log = wire([(0, 100.0), (3, 150.0)], cap={_day(2): 50.0})
    twr, dd = dashboard._twr(log.equity_df("b1"), "b1", "live")
    assert twr == pytest.approx(0.0) and dd == pytest.approx(0.0)
    pnl = {s["date"]: s["pnl"] for s in dashboard.api_daily_pnl(parse_qs("bot=b1"))["series"]}
    assert pnl == {_day(3): pytest.approx(0.0)}


def test_sparkline_subtracts_net_deposits(wire):
    log = wire([(0, 100.0), (1, 110.0), (2, 160.0), (3, 176.0)], cap={_day(2): 50.0})
    s = dashboard._pnl_series(log.equity_df("b1"), "b1", "live")
    assert [p["pnl"] for p in s["points"]] == pytest.approx([0.0, 10.0, 10.0, 26.0])
    assert s["last_pct"] == pytest.approx(21.0)


def test_paper_events_do_not_touch_live_ledger(wire):
    wire([(0, 100.0), (1, 120.0)], cap={_day(1): 50.0}, ledger="paper")
    curve = [p["equity"] for p in dashboard.api_equity(parse_qs("bot=b1"))["series"]]
    assert curve == pytest.approx([100.0, 120.0])
