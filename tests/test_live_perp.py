"""Perp live executor tests: long/short, kill liquidation, baseline isolation, rejects, key checks (fake exchange)."""
from __future__ import annotations

import json

import pytest

from core.models import Side, Signal
from strategies.base import Strategy
from safety.guard import RiskConfig
from execution.live_perp import LivePerpTrader


class FakeFut:
    def __init__(self, wallet=2500.0, price=100.0, base_pos=0.0, hedge=False):
        self.wallet = wallet
        self.price = price
        self.pos = base_pos          # exchange net perp position (signed)
        self.hedge = hedge
        self.orders = []             # (side, qty[, positionSide])
        self.params_log = []         # params dict of every order that reached the exchange
        self.leverage_set = None
        self.fail_orders_from = None  # index of the first create_order call that raises
        self.fill_ratio = 1.0         # fraction of each order that fills
        self.ignore_reduce_only = False  # simulate an order that leaves a residual
        self._calls = 0

    def sapi_get_account_apirestrictions(self):
        return {"enableWithdrawals": "false", "permitsUniversalTransfer": "false",
                "enableInternalTransfer": False, "enableMargin": False,
                "enableVanillaOptions": False, "enableReading": True, "ipRestrict": True,
                "enableFutures": "true"}

    def fapiPrivateGetPositionSideDual(self):  # noqa: N802
        return {"dualSidePosition": "true" if self.hedge else "false"}

    def set_margin_mode(self, mode, sym):
        return {}

    def set_leverage(self, lev, sym):
        self.leverage_set = lev
        return {}

    def amount_to_precision(self, sym, q):
        return f"{q:.6f}"

    def fetch_ticker(self, sym):
        return {"last": self.price}

    def fetch_balance(self):
        return {"total": {"USDT": self.wallet}, "free": {"USDT": self.wallet}, "used": {}}

    def fetch_positions(self, syms=None):
        if abs(self.pos) < 1e-12:
            return []
        return [{"contracts": abs(self.pos), "side": "short" if self.pos < 0 else "long"}]

    def create_order(self, sym, typ, side, qty, params=None):
        import ccxt
        self._calls += 1
        if self.fail_orders_from is not None and self._calls > self.fail_orders_from:
            raise ccxt.ExchangeError("simulated order failure")
        qty = float(qty) * self.fill_ratio
        self.params_log.append(dict(params or {}))
        pos_side = (params or {}).get("positionSide")
        if self.hedge and pos_side is None:
            raise RuntimeError('-4061: Order\'s position side does not match user\'s setting')
        self.pos += qty if side == "buy" else -qty
        self.orders.append((side, qty, pos_side) if self.hedge else (side, qty))
        return {"filled": qty, "average": self.price}


class Bot(Strategy):
    def __init__(self, name="perp-t", symbol="BTC/USDT", timeframe="4h"):
        super().__init__(name, symbol, timeframe, {})
        self.queue = []

    def on_bar(self, bar, account):
        return self.queue.pop(0) if self.queue else []


def bar(h, p):
    return [h * 3600_000, p, p, p, p, 1.0]


RISK = RiskConfig(allow_short=True, max_leverage=3, max_order_notional=250_000,
                  allowed_symbols=["BTC/USDT"])


@pytest.fixture()
def trader(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0)
    t = LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                       max_leverage=3, leverage=2, exchange=ex, live_dir=tmp_path)
    return t, ex


def test_open_long_places_order_and_books(trader):
    t, ex = trader
    t.bot.queue = [[Signal(Side.BUY, 5.0)]]   # notional 500 < cap * lev
    t.step(bar(1, 100))
    assert ex.orders and ex.orders[-1][0] == "buy"
    assert t.portfolio.position_qty == pytest.approx(5.0)


def test_open_short_signed(trader):
    t, ex = trader
    t.bot.queue = [[Signal(Side.SELL, 5.0)]]
    t.step(bar(1, 100))
    assert t.portfolio.position_qty == pytest.approx(-5.0)
    assert t.portfolio.position_qty < 0  # real short


def test_kill_liquidates_and_writes_kill_file(trader, tmp_path):
    t, ex = trader
    t.bot.queue = [[Signal(Side.SELL, 7.5)]]   # notional 750 (75% of equity), short
    t.step(bar(1, 100))
    assert t.portfolio.position_qty < 0
    t.bot.queue = [[]]
    ex.price = 140.0  # short loses 7.5*40=300 / 1000 = 30% -> drawdown > 15% kill
    with pytest.raises(SystemExit, match="(?i)kill switch"):
        t.step(bar(2, 130))
    assert t.kill_file.exists()
    assert t.portfolio.position_qty == pytest.approx(0.0)  # flattened


def test_baseline_excludes_existing_position(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0, base_pos=12.5)  # account already long 12.5 BTC (not this bot)
    t = LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                       max_leverage=3, leverage=2, exchange=ex, live_dir=tmp_path)
    assert t._baseline_perp == pytest.approx(12.5)
    assert t._perp_position() == pytest.approx(0.0)  # this bot's position = 0


def test_leverage_cap_rejects(trader):
    t, ex = trader
    t.bot.queue = [[Signal(Side.BUY, 250.0)]]  # notional 25000 >> 1000*3
    t.step(bar(1, 100))
    df = t.trade_log.events_df()
    assert (df["kind"] == "reject").any()


def _hedge_trader(tmp_path, base_pos=0.0):
    ex = FakeFut(wallet=2500.0, price=100.0, base_pos=base_pos, hedge=True)
    t = LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                       max_leverage=3, leverage=2, exchange=ex, live_dir=tmp_path)
    return t, ex


def test_hedge_mode_detected(tmp_path):
    t, ex = _hedge_trader(tmp_path)
    assert t._hedge is True


def test_hedge_open_long_uses_long_side(tmp_path):
    t, ex = _hedge_trader(tmp_path)
    t.bot.queue = [[Signal(Side.BUY, 5.0)]]
    t.step(bar(1, 100))
    assert ex.orders[-1] == ("buy", 5.0, "LONG")  # no -4061 rejection
    assert t.portfolio.position_qty == pytest.approx(5.0)


def test_hedge_open_short_uses_short_side(tmp_path):
    t, ex = _hedge_trader(tmp_path)
    t.bot.queue = [[Signal(Side.SELL, 5.0)]]
    t.step(bar(1, 100))
    assert ex.orders[-1] == ("sell", 5.0, "SHORT")
    assert t.portfolio.position_qty == pytest.approx(-5.0)


def test_hedge_close_long_uses_long_side(tmp_path):
    t, ex = _hedge_trader(tmp_path)
    t.bot.queue = [[Signal(Side.BUY, 7.5)]]   # open long
    t.step(bar(1, 100))
    t.bot.queue = [[Signal(Side.SELL, 7.5)]]  # then close it
    t.step(bar(2, 100))
    assert ex.orders[-1] == ("sell", 7.5, "LONG")  # closing a long requires positionSide=LONG
    assert t.portfolio.position_qty == pytest.approx(0.0)


def test_hedge_flip_splits_into_two_orders(tmp_path):
    t, ex = _hedge_trader(tmp_path)
    # net long 5, SELL 12.5 -> close long 5 (LONG) + open short 7.5 (SHORT)
    subs = t._suborders(Side.SELL, 12.5, 5.0)
    assert subs == [("sell", "LONG", 5.0), ("sell", "SHORT", 7.5)]


def test_one_way_mode_no_position_side(tmp_path):
    ex = FakeFut(hedge=False)
    t = LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                       max_leverage=3, leverage=2, exchange=ex, live_dir=tmp_path)
    assert t._hedge is False
    t.bot.queue = [[Signal(Side.BUY, 5.0)]]
    t.step(bar(1, 100))
    assert ex.orders[-1] == ("buy", 5.0)  # one-way: no positionSide


def test_accounting_and_peak_persist_across_restart(tmp_path):
    """cash/realized/fees/peak_equity must survive a restart; otherwise equity is
    understated and the kill drawdown peak resets (losses could exceed the limit)."""
    ex = FakeFut(wallet=2500.0, price=100.0)
    t1 = LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                        max_leverage=3, leverage=2, exchange=ex, live_dir=tmp_path)
    t1.bot.queue = [[Signal(Side.BUY, 5.0)]]
    t1.step(bar(1, 100))
    ex.price = 110.0
    t1.bot.queue = [[Signal(Side.SELL, 5.0)]]   # close long at a profit
    t1.step(bar(2, 110))
    assert t1.portfolio.realized_pnl > 0
    cash1, rp1, peak1 = t1.portfolio.cash, t1.portfolio.realized_pnl, t1.guard.peak_equity
    assert peak1 > 1000.0  # peak was raised

    # simulated restart: new instance, same live_dir and state file
    t1.close()  # release the single-instance lock, as process exit would
    t2 = LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                        max_leverage=3, leverage=2, exchange=FakeFut(price=110.0), live_dir=tmp_path)
    assert t2.portfolio.cash == pytest.approx(cash1)
    assert t2.portfolio.realized_pnl == pytest.approx(rp1)
    assert t2.guard.peak_equity == pytest.approx(peak1)  # drawdown peak not reset


def _softcut_trader(tmp_path, soft_pct=0.12, max_loss=4):
    ex = FakeFut(wallet=2500.0, price=100.0)
    t = LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                       max_leverage=3, leverage=2, exchange=ex, live_dir=tmp_path,
                       soft_cut_pct=soft_pct, max_consec_losses=max_loss)
    return t, ex


def test_soft_cut_on_drawdown_flattens_and_writes_marker(tmp_path):
    t, ex = _softcut_trader(tmp_path, soft_pct=0.12)
    t.bot.queue = [[Signal(Side.BUY, 7.5)]]
    t.step(bar(1, 100))            # open long, peak ~1000
    ex.price = 83.0               # unrealized loss 7.5*17=127.5 -> drawdown ~12.8% >= 12%
    t.bot.queue = [[]]
    with pytest.raises(SystemExit, match="(?i)soft cut"):
        t.step(bar(2, 83))
    assert t.softcut_file.exists()
    assert t.portfolio.position_qty == pytest.approx(0.0)  # flattened


def test_soft_cut_persists_final_state(tmp_path):
    """Regression: after a soft-cut close the final state must be persisted. step() only
    saves at its end, which the SystemExit skips; the state file would otherwise freeze
    before the close (phantom position, missing realized loss)."""
    t, ex = _softcut_trader(tmp_path, soft_pct=0.12)
    t.bot.queue = [[Signal(Side.BUY, 7.5)]]
    t.step(bar(1, 100))
    assert json.loads(t.state_file.read_text())["position_qty"] > 0  # position exists before the close
    ex.price = 83.0
    t.bot.queue = [[]]
    with pytest.raises(SystemExit, match="(?i)soft cut"):
        t.step(bar(2, 83))
    s = json.loads(t.state_file.read_text())
    assert s["position_qty"] == pytest.approx(0.0)                        # no phantom position
    assert s["realized_pnl"] == pytest.approx(t.portfolio.realized_pnl)   # realized loss booked
    assert s["cash"] == pytest.approx(t.portfolio.cash)
    assert s["last_bar_ts"] == bar(2, 83)[0]                             # halting bar is not replayed


def test_kill_persists_final_state(tmp_path):
    """Same as above, hard kill path."""
    t, ex = _softcut_trader(tmp_path, soft_pct=None, max_loss=99)
    t.bot.queue = [[Signal(Side.SELL, 7.5)]]
    t.step(bar(1, 100))
    ex.price = 140.0
    t.bot.queue = [[]]
    with pytest.raises(SystemExit, match="(?i)kill switch"):
        t.step(bar(2, 130))
    s = json.loads(t.state_file.read_text())
    assert s["position_qty"] == pytest.approx(0.0)
    assert s["realized_pnl"] == pytest.approx(t.portfolio.realized_pnl)
    assert s["last_bar_ts"] == bar(2, 130)[0]


def test_soft_cut_on_losing_streak(tmp_path):
    t, ex = _softcut_trader(tmp_path, soft_pct=None, max_loss=4)
    t._consec_losses = 4
    t.bot.queue = [[]]
    with pytest.raises(SystemExit, match="(?i)soft cut"):
        t.step(bar(1, 100))
    assert t.softcut_file.exists()


def test_soft_cut_marker_blocks_startup(tmp_path):
    (tmp_path / "perp-t.softcut").write_text("ts=1 reason=test\n")
    with pytest.raises(SystemExit, match="(?i)soft cut"):
        LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                       max_leverage=3, leverage=2, exchange=FakeFut(), live_dir=tmp_path,
                       soft_cut_pct=0.12, max_consec_losses=4)


def test_losing_streak_persists_across_restart(tmp_path):
    t, ex = _softcut_trader(tmp_path, soft_pct=None, max_loss=10)
    t.bot.queue = [[Signal(Side.BUY, 7.5)]]; t.step(bar(1, 100))
    ex.price = 90.0
    t.bot.queue = [[Signal(Side.SELL, 7.5)]]; t.step(bar(2, 90))  # losing close -> consec=1
    assert t._consec_losses == 1
    t.close()
    t2 = LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                        max_leverage=3, leverage=2, exchange=FakeFut(price=90.0), live_dir=tmp_path,
                        soft_cut_pct=None, max_consec_losses=10)
    assert t2._consec_losses == 1


def test_funding_sync_books_settled_funding(tmp_path):
    """Exchange-settled funding must be booked into local cash/funding_paid, else accounts drift."""
    ex = FakeFut(wallet=2500.0, price=100.0)
    ex.fetch_funding_history = lambda sym, since=None: [
        {"timestamp": 7_200_000, "amount": -1.25},   # paid 1.25
        {"timestamp": 10_800_000, "amount": 0.5},    # received 0.5
    ]
    t = LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                       max_leverage=3, leverage=2, exchange=ex, live_dir=tmp_path)
    t.bot.queue = [[], []]
    t.step(bar(1, 100))   # first run: record start only, no backfill
    assert t.portfolio.cash == pytest.approx(1000.0)
    t.step(bar(4, 100))   # second bar: settles -1.25+0.5=-0.75
    assert t.portfolio.cash == pytest.approx(999.25)
    assert t.portfolio.funding_paid == pytest.approx(0.75)  # positive = net paid
    assert t.state["funding_synced_ts"] == 10_800_000


def test_dangerous_key_refused(tmp_path):
    ex = FakeFut()
    ex.sapi_get_account_apirestrictions = lambda: {
        "enableWithdrawals": "true", "permitsUniversalTransfer": "false", "enableFutures": "true"}
    with pytest.raises(SystemExit, match="withdrawals"):
        LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK,
                       max_leverage=3, leverage=2, exchange=ex, live_dir=tmp_path)


def test_retire_flattens_persists_and_writes_marker(trader):
    """scripts/retire_live_perp.retire: reconcile -> flatten -> persist final state -> softcut blocks restart."""
    import sys
    sys.path.insert(0, "scripts")
    from retire_live_perp import retire

    t, ex = trader
    t.bot.queue = [[Signal(Side.SELL, 5.0)]]
    t.step(bar(1, 100))                        # open short 5
    assert t.portfolio.position_qty == pytest.approx(-5.0)
    retire(t, bar(2, 100), "test retire")       # SystemExit is swallowed inside retire
    assert t.softcut_file.exists()
    assert "test retire" in t.softcut_file.read_text()
    assert t.portfolio.position_qty == pytest.approx(0.0)          # flattened
    assert ex.pos == pytest.approx(0.0)                            # exchange side flat too
    s = json.loads(t.state_file.read_text())
    assert s["position_qty"] == pytest.approx(0.0)                 # final state persisted, no phantom position
    assert s["last_bar_ts"] == bar(2, 100)[0]


def test_retire_uses_exchange_when_local_state_is_stale(trader):
    """retire reconciles first: local state claims a position but the exchange is flat ->
    close quantity follows the exchange (0) and no extra order is sent."""
    import sys
    sys.path.insert(0, "scripts")
    from retire_live_perp import retire

    t, ex = trader
    t.portfolio.position_qty = -12.5           # fake phantom short; exchange is flat
    retire(t, bar(1, 100), "test phantom retire")
    assert t.portfolio.position_qty == pytest.approx(0.0)
    assert ex.orders == []                     # no real order sent
    assert t.softcut_file.exists()


# ---- startup gates, leverage and margin setup ----

def _mk(tmp_path, ex=None, **kw):
    args = dict(capital=1000.0, fee_bps=5, slippage_bps=0, risk=RISK, max_leverage=3,
                leverage=2, exchange=ex or FakeFut(), live_dir=tmp_path, stage="live")
    args.update(kw)
    return LivePerpTrader(Bot(), **args)


def test_stage_must_be_live(tmp_path):
    from execution.halt import TraderHalted
    with pytest.raises(TraderHalted, match="stage"):
        _mk(tmp_path, stage=None)
    with pytest.raises(TraderHalted, match="stage"):
        _mk(tmp_path, stage="paper")


def test_missing_symbol_whitelist_refused(tmp_path):
    from execution.halt import TraderHalted
    for syms in (None, []):
        with pytest.raises(TraderHalted, match="allowed_symbols"):
            _mk(tmp_path, risk=RiskConfig(allow_short=True, max_leverage=3, allowed_symbols=syms))


def test_leverage_clamped_to_hard_cap(tmp_path):
    ex = FakeFut()
    t = _mk(tmp_path, ex, leverage=10, max_leverage=5)
    assert t.leverage == 3 and ex.leverage_set == 3
    ex2 = FakeFut()
    t2 = _mk(tmp_path / "b", ex2, leverage=3, max_leverage=2)
    assert t2.leverage == 2 and ex2.leverage_set == 2


def test_margin_already_isolated_is_fine(tmp_path):
    ex = FakeFut()

    def already(mode, sym):
        raise Exception('binance {"code":-4046,"msg":"No need to change margin type."}')
    ex.set_margin_mode = already
    _mk(tmp_path, ex)  # does not raise


def test_margin_mode_error_fails_closed(tmp_path):
    from execution.halt import TraderHalted
    ex = FakeFut()

    def boom(mode, sym):
        raise Exception('binance {"code":-4048,"msg":"Margin type cannot be changed if there exists position."}')
    ex.set_margin_mode = boom
    with pytest.raises(TraderHalted, match="isolated margin"):
        _mk(tmp_path, ex)


def test_set_leverage_error_fails_closed(tmp_path):
    from execution.halt import TraderHalted
    ex = FakeFut()

    def boom(lev, sym):
        raise Exception("network down")
    ex.set_leverage = boom
    with pytest.raises(TraderHalted, match="leverage"):
        _mk(tmp_path, ex)


def test_leverage_readback_mismatch_refused(tmp_path):
    from execution.halt import TraderHalted
    ex = FakeFut()
    ex.fetch_leverage = lambda sym: {"longLeverage": 5, "shortLeverage": 5}
    with pytest.raises(TraderHalted, match="verification failed"):
        _mk(tmp_path, ex)


def test_margin_mode_readback_mismatch_refused(tmp_path):
    from execution.halt import TraderHalted
    ex = FakeFut()
    ex.fetch_margin_mode = lambda sym: {"marginMode": "cross"}
    with pytest.raises(TraderHalted, match="verification failed"):
        _mk(tmp_path, ex)


def test_readback_match_starts(tmp_path):
    ex = FakeFut()
    ex.fetch_leverage = lambda sym: {"longLeverage": 2, "shortLeverage": 2}
    ex.fetch_margin_mode = lambda sym: {"marginMode": "isolated"}
    _mk(tmp_path, ex)  # does not raise


def test_halt_maps_to_exit_code():
    from execution.halt import HALT_EXIT_CODE, TraderHalted, exit_on_halt

    def run():
        raise TraderHalted("test halt")
    with pytest.raises(SystemExit) as ei:
        exit_on_halt(run)
    assert ei.value.code == HALT_EXIT_CODE


# ---- hedge-mode split orders ----

def test_hedge_flip_aborts_opening_leg_when_close_fails(tmp_path):
    t, ex = _hedge_trader(tmp_path)
    t.bot.queue = [[Signal(Side.BUY, 5.0)]]
    t.step(bar(1, 100))                       # long 5
    ex.fail_orders_from = ex._calls           # every further order fails
    t.bot.queue = [[Signal(Side.SELL, 12.5)]]  # would be close 5 + open short 7.5
    t.step(bar(2, 100))
    assert ex.orders == [("buy", 5.0, "LONG")]  # neither leg went out after the failure
    assert t.portfolio.position_qty == pytest.approx(5.0)


def test_hedge_flip_partial_close_skips_opening_leg(tmp_path):
    t, ex = _hedge_trader(tmp_path)
    t.bot.queue = [[Signal(Side.BUY, 5.0)]]
    t.step(bar(1, 100))
    ex.fill_ratio = 0.5                       # closing leg only half fills
    t.bot.queue = [[Signal(Side.SELL, 12.5)]]
    t.step(bar(2, 100))
    assert [o[2] for o in ex.orders] == ["LONG", "LONG"]  # no SHORT opening leg
    assert t.portfolio.position_qty == pytest.approx(2.5)


# ---- flatten paths ----

def test_kill_flatten_uses_reduce_only_one_way(trader):
    t, ex = trader
    t.bot.queue = [[Signal(Side.SELL, 7.5)]]
    t.step(bar(1, 100))
    assert "reduceOnly" not in ex.params_log[-1]   # normal order is not reduce-only
    t.bot.queue = [[]]
    ex.price = 140.0
    with pytest.raises(SystemExit, match="(?i)kill switch"):
        t.step(bar(2, 130))
    assert ex.params_log[-1].get("reduceOnly") is True
    assert ex.orders[-1] == ("buy", 7.5)


def test_flatten_in_hedge_mode_uses_position_side(tmp_path):
    t, ex = _hedge_trader(tmp_path)
    t.bot.queue = [[Signal(Side.SELL, 7.5)]]
    t.step(bar(1, 100))
    from core.models import Bar
    halt_bar = Bar(ts=2 * 3600_000, open=100, high=100, low=100, close=100, volume=1,
                   symbol="BTC/USDT", timeframe="4h")
    with pytest.raises(SystemExit, match="(?i)soft cut"):
        t._soft_cut(halt_bar, "test")
    assert ex.orders[-1] == ("buy", 7.5, "SHORT")
    assert "reduceOnly" not in ex.params_log[-1]


def test_flatten_residual_raises_critical_alert(trader, monkeypatch):
    t, ex = trader
    alerts = []
    monkeypatch.setattr("execution.live_perp.send_alert", lambda m: alerts.append(m))
    t.bot.queue = [[Signal(Side.SELL, 7.5)]]
    t.step(bar(1, 100))
    ex.fill_ratio = 0.5                      # liquidation only half fills
    t.bot.queue = [[]]
    ex.price = 140.0
    with pytest.raises(SystemExit, match="FLATTEN INCOMPLETE"):
        t.step(bar(2, 130))
    assert t.portfolio.position_qty == pytest.approx(-3.75)   # residual adopted, not claimed flat
    assert any(a.startswith("[CRITICAL]") and "residual" in a for a in alerts)
    assert "FLATTEN INCOMPLETE" in t.kill_file.read_text()
    df = t.trade_log.events_df()
    assert (df["kind"] == "flatten_incomplete").any()
    assert json.loads(t.state_file.read_text())["position_qty"] == pytest.approx(-3.75)


# ---- dust on flatten / transient setup errors ----

class DustFut(FakeFut):
    """amount_to_precision raises InvalidOrder below the step, like ccxt does."""

    def amount_to_precision(self, sym, q):
        import ccxt
        if abs(float(q)) < 0.001:
            raise ccxt.InvalidOrder(f"amount of {sym} must be greater than minimum precision")
        return f"{float(q):.3f}"


def _halt_bar():
    from core.models import Bar
    return Bar(ts=2 * 3600_000, open=100, high=100, low=100, close=100, volume=1,
               symbol="BTC/USDT", timeframe="4h")


@pytest.mark.parametrize("path", ["kill", "softcut"])
def test_flatten_dust_treated_as_flat_and_marker_written(tmp_path, path):
    from execution.halt import HALT_EXIT_CODE, TraderHalted
    ex = DustFut()
    t = _mk(tmp_path, ex)
    ex.pos = -0.0004
    t.portfolio.position_qty = -0.0004
    with pytest.raises(TraderHalted) as ei:
        if path == "kill":
            t.guard.kill_reason = "test"
            t._kill(_halt_bar())
        else:
            t._soft_cut(_halt_bar(), "test")
    assert "FLATTEN" not in str(ei.value)
    assert ex.orders == []                          # nothing sendable went out
    marker = t.kill_file if path == "kill" else t.softcut_file
    assert marker.exists() and "FLATTEN" not in marker.read_text()
    with pytest.raises(SystemExit) as se:
        from execution.halt import exit_on_halt

        def run():
            raise ei.value
        exit_on_halt(run)
    assert se.value.code == HALT_EXIT_CODE


def test_non_closing_invalid_order_still_raises(tmp_path):
    import ccxt
    from core.models import Order
    t = _mk(tmp_path, DustFut())
    with pytest.raises(ccxt.InvalidOrder):
        t._place(Order(1, "BTC/USDT", Side.BUY, 0.0004, "perp-t"))


@pytest.mark.parametrize("method", ["set_margin_mode", "set_leverage"])
@pytest.mark.parametrize("err", ["NetworkError", "RequestTimeout"])
def test_transient_setup_error_is_ordinary_error(tmp_path, method, err):
    import ccxt
    from execution.halt import TraderHalted
    ex = FakeFut()

    def boom(*a, **k):
        raise getattr(ccxt, err)("transient")
    setattr(ex, method, boom)
    with pytest.raises(getattr(ccxt, err)) as ei:
        _mk(tmp_path, ex)
    assert not isinstance(ei.value, TraderHalted)


@pytest.mark.parametrize("method", ["set_margin_mode", "set_leverage"])
def test_exchange_rejection_on_setup_fails_closed(tmp_path, method):
    import ccxt
    from execution.halt import TraderHalted
    ex = FakeFut()

    def boom(*a, **k):
        raise ccxt.ExchangeError("rejected by exchange")
    setattr(ex, method, boom)
    with pytest.raises(TraderHalted):
        _mk(tmp_path, ex)
