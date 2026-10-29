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
    logs = []
    def _wire(rows, cap=None, ledger="live"):
        tl = TradeLog(tmp_path / "ledger.sqlite")
        for n, eq in rows:
            tl.record_equity(T0 + n * DAY, "b1", eq, eq, 0.0, 0.0)
        tl.close()
        log = TradeLog(tmp_path / "ledger.sqlite")
        logs.append(log)
        monkeypatch.setattr(dashboard, "_open_logs", lambda: (log, log))
        monkeypatch.setattr(dashboard, "_close", lambda *a: None)
        monkeypatch.setattr(dashboard, "_log_for", lambda *a: log)
        monkeypatch.setattr(dashboard, "_stage_of", lambda n: "live")
        monkeypatch.setattr(dashboard, "_capital_events",
                            lambda: {(d, "b1", ledger): v for d, v in (cap or {}).items()})
        bars = pd.DataFrame({"ts": [T0 - DAY], "close": [1.0]})
        monkeypatch.setattr(dashboard, "load_bars", lambda *a, **k: bars)
        return log
    yield _wire
    for log in logs:
        log.close()


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


def test_overview_and_curve_include_first_day_returns(wire, monkeypatch):
    log = wire([])
    start = pd.Timestamp(_day(0), tz="UTC")
    for hours, equity in [(0, 100.0), (12, 110.0), (36, 160.0)]:
        ts = int((start + pd.Timedelta(hours=hours)).timestamp() * 1000)
        log.record_equity(ts, "b1", equity, equity, 0.0, 0.0)
    monkeypatch.setattr(dashboard, "_capital_events", lambda: {(_day(1), "b1", "live"): 50.0})
    monkeypatch.setattr(dashboard, "_bots", lambda: [{"name": "b1", "stage": "live"}])
    monkeypatch.setattr(dashboard, "last_beat", lambda name: None)
    card = dashboard.api_overview({})["bots"][0]
    curve = dashboard.api_equity(parse_qs("bot=b1"))["series"]
    assert card["pnl"] == pytest.approx(10.0)
    assert card["pnl_pct"] == pytest.approx(10.0)
    assert curve[-1]["equity"] == pytest.approx(110.0)


def test_live_summary_does_not_subtract_events_after_last_snapshot(wire, monkeypatch, tmp_path):
    log = wire([])
    today = pd.Timestamp.now(tz="UTC").normalize()
    for days, equity in [(9, 100.0), (2, 150.0)]:
        ts = int((today - pd.Timedelta(days=days)).timestamp() * 1000)
        log.record_equity(ts, "b1", equity, equity, 0.0, 0.0)
    monkeypatch.setattr(dashboard, "_capital_events", lambda: {
        ((today - pd.Timedelta(days=4)).strftime("%Y-%m-%d"), "b1", "live"): 50.0,
        (today.strftime("%Y-%m-%d"), "b1", "live"): 30.0,
    })
    monkeypatch.setattr(dashboard, "_bots", lambda: [{"name": "b1", "stage": "live"}])
    monkeypatch.setattr(dashboard, "_live_db", lambda: tmp_path / "ledger.sqlite")
    summary = dashboard.api_live_summary({})
    assert summary["day_chg"] == pytest.approx(0.0)
    assert summary["week_chg"] == pytest.approx(0.0)


def test_sparkline_keeps_latest_snapshot_when_downsampling(wire):
    log = wire([(n, 100.0 + n) for n in range(242)])
    s = dashboard._pnl_series(log.equity_df("b1"), "b1", "live")
    assert len(s["points"]) <= 120
    assert s["points"][-1]["ts"] == T0 + 241 * DAY
    assert s["points"][-1]["pnl"] == s["last"] == pytest.approx(241.0)


@pytest.mark.parametrize("delta", [50.0, -50.0])
def test_capital_move_without_performance_has_zero_return(wire, delta):
    log = wire([(0, 100.0), (1, 100.0 + delta)], cap={_day(1): delta})
    assert dashboard._twr(log.equity_df("b1"), "b1", "live") == pytest.approx((0.0, 0.0))


def test_intraday_return_and_drawdown_use_all_snapshots(wire):
    log = wire([])
    for hours, equity in [(0, 100.0), (1, 120.0), (2, 90.0)]:
        log.record_equity(T0 + hours * 3_600_000, "b1", equity, equity, 0.0, 0.0)
    assert dashboard._twr(log.equity_df("b1"), "b1", "live") == pytest.approx((-0.1, 0.25))
