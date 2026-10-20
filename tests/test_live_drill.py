"""Kill-switch drill: wiring tests for the spot live executor (required before going live).

RiskGuard has its own unit tests; this drill verifies LiveTrader's real wiring:
rejected orders never reach the exchange, a kill liquidates + persists + blocks restart,
revival allows startup again, and reconciliation adopts the exchange value.

Run alone: uv run pytest tests/test_live_drill.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.models import AccountView, Bar, Side, Signal
from strategies.base import Strategy
from safety.guard import RiskConfig
from execution.live import LiveTrader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


class FakeExchange:
    """Minimal Binance stand-in: in-memory ledger, instant full fills, records every order that reaches it."""

    def __init__(self, usdt: float = 100.0, base: str = "BTC", dust: float = 0.0):
        self.balances = {"USDT": usdt, base: dust}
        self.base = base
        self.orders: list[dict] = []
        self.price = 100.0  # fill reference price, driven by tests

    # --- interface used by LiveTrader ---
    def sapi_get_account_apirestrictions(self):
        return {"enableWithdrawals": "false", "permitsUniversalTransfer": "false",
                "enableInternalTransfer": False, "enableMargin": False,
                "enableVanillaOptions": False, "enableReading": True,
                "ipRestrict": "true", "enableSpotAndMarginTrading": "true"}

    def load_markets(self):
        return {}

    def amount_to_precision(self, symbol, qty):
        return f"{qty:.8f}"

    def fetch_balance(self):
        return {"total": dict(self.balances), "free": dict(self.balances)}

    def create_order(self, symbol, type_, side, qty, price=None, params=None):
        self.params_log = getattr(self, "params_log", []) + [dict(params or {})]
        qty = float(qty)
        cost = qty * self.price
        fee = cost * 0.001
        if side == "buy":
            assert self.balances["USDT"] >= cost, "FakeExchange: insufficient balance"
            self.balances["USDT"] -= cost + fee
            self.balances[self.base] += qty
        else:
            assert self.balances[self.base] >= qty * (1 - 1e-9), "FakeExchange: insufficient position"
            self.balances[self.base] -= qty
            self.balances["USDT"] += cost - fee
        order = {"id": str(len(self.orders)), "filled": qty, "average": self.price,
                 "trades": [{"fee": {"cost": fee, "currency": "USDT"}}]}
        self.orders.append({"side": side, "qty": qty, "price": self.price})
        return order


class DrillStrategy(Strategy):
    """Scripted drill strategy: emits whatever is queued."""

    def __init__(self, name="drill-bot", symbol="BTC/USDT", timeframe="1h", params=None):
        super().__init__(name, symbol, timeframe, params or {})
        self.queue: list[list[Signal]] = []

    def on_bar(self, bar: Bar, account: AccountView) -> list[Signal]:
        return self.queue.pop(0) if self.queue else []


def make_bar(ts_hour: int, price: float) -> list:
    return [ts_hour * 3_600_000, price, price, price, price, 1.0]


RISK = RiskConfig(max_order_notional=50, allowed_symbols=["BTC/USDT"])


@pytest.fixture()
def trader(tmp_path):
    ex = FakeExchange(usdt=100.0)
    bot = DrillStrategy()
    t = LiveTrader(bot, stage="live", initial_cash=100.0, risk=RISK, exchange=ex, live_dir=tmp_path)
    ex.price = 100.0
    return t


def test_oversized_order_rejected_before_exchange(trader):
    trader.bot.queue = [[Signal(Side.BUY, 1.0)]]  # notional 100 > cap 50
    trader.step(make_bar(1, 100.0))
    assert trader.exchange.orders == [], "rejection must happen before reaching the exchange"
    events = trader.trade_log.events_df()
    assert (events["kind"] == "reject").any()


def test_symbol_outside_whitelist_rejected(trader, tmp_path):
    ex = FakeExchange(usdt=100.0, base="DOGE")
    bot = DrillStrategy(name="drill-doge", symbol="DOGE/USDT")
    t = LiveTrader(bot, stage="live", initial_cash=100.0, risk=RISK, exchange=ex, live_dir=tmp_path)
    bot.queue = [[Signal(Side.BUY, 0.2)]]
    t.step(make_bar(1, 100.0))
    assert ex.orders == []


def test_normal_buy_fills_and_books(trader):
    trader.bot.queue = [[Signal(Side.BUY, 0.3)]]  # notional 30 < 50
    trader.step(make_bar(1, 100.0))
    assert len(trader.exchange.orders) == 1
    assert trader.state["position_qty"] == pytest.approx(0.3)
    assert trader.state["avg_cost"] > 100.0  # buy fee folded into cost


def test_kill_liquidates_persists_blocks_restart_and_revives(trader, tmp_path):
    trader.bot.queue = [[Signal(Side.BUY, 0.3)]]
    trader.step(make_bar(1, 100.0))

    # position is 30% of equity; price must fall to 40 for a >15% drawdown -> kill
    trader.exchange.price = 40.0
    with pytest.raises(SystemExit, match="kill switch halt"):
        trader.step(make_bar(2, 40.0))

    assert trader.kill_file.exists(), "kill must be persisted"
    sells = [o for o in trader.exchange.orders if o["side"] == "sell"]
    assert sells and sells[-1]["qty"] == pytest.approx(0.3), "kill must liquidate"

    with pytest.raises(SystemExit, match="revive manually"):  # restart blocked by kill file
        LiveTrader(DrillStrategy(), stage="live", initial_cash=100.0, risk=RISK,
                   exchange=trader.exchange, live_dir=tmp_path)

    # Deleting only the kill file is not enough: the kill is also in the saved state.
    trader.kill_file.unlink()
    with pytest.raises(SystemExit, match="active kill"):
        LiveTrader(DrillStrategy(), stage="live", initial_cash=100.0, risk=RISK,
                   exchange=trader.exchange, live_dir=tmp_path)
    trader.kill_file.write_text("ts=1 reason=test\n")

    from revive import revive_bot
    revive_bot(tmp_path, "drill-bot", "drill: reviewed the cause, safe to resume")
    t2 = LiveTrader(DrillStrategy(), stage="live", initial_cash=100.0, risk=RISK,
                    exchange=trader.exchange, live_dir=tmp_path)  # no longer raises
    assert not t2.guard.killed
    # Peak re-baselines at the next mark: no immediate re-trip against the pre-kill peak.
    t2.step(make_bar(3, 40.0))
    assert not t2.guard.killed and t2.guard.peak_equity < 100.0


def test_reconcile_adopts_exchange(trader):
    trader.bot.queue = [[Signal(Side.BUY, 0.3)]]
    trader.step(make_bar(1, 100.0))
    # small out-of-band change (small enough not to trigger the drawdown kill)
    trader.exchange.balances["BTC"] -= 0.02
    trader.step(make_bar(2, 100.0))
    assert trader.state["position_qty"] == pytest.approx(0.28)
    events = trader.trade_log.events_df()
    assert (events["kind"] == "reconcile").any()


def test_dangerous_key_refused(tmp_path):
    ex = FakeExchange()
    ex.sapi_get_account_apirestrictions = lambda: {
        "enableWithdrawals": "true", "permitsUniversalTransfer": "false",
        "ipRestrict": "true", "enableSpotAndMarginTrading": "true"}
    with pytest.raises(SystemExit, match="withdrawals"):
        LiveTrader(DrillStrategy(), stage="live", initial_cash=100.0, risk=RISK,
                   exchange=ex, live_dir=tmp_path)


def test_startup_auth_failure_alerts_with_outbound_ip(tmp_path, monkeypatch):
    # A -2015 at startup must become an actionable alert carrying the outbound IP
    # plus a SystemExit, rather than an unexplained crash.
    import execution.live as live_mod
    monkeypatch.setattr(live_mod, "_outbound_ip", lambda: "198.51.100.4")
    ex = FakeExchange()

    def boom():
        raise Exception('binance {"code":-2015,"msg":"Invalid API-key, IP, '
                        'or permissions for action."}')

    ex.sapi_get_account_apirestrictions = boom
    with pytest.raises(SystemExit, match="198.51.100.4"):
        LiveTrader(DrillStrategy(), stage="live", initial_cash=100.0, risk=RISK,
                   exchange=ex, live_dir=tmp_path)


def test_baseline_excludes_existing_dust(tmp_path):
    ex = FakeExchange(usdt=100.0, dust=0.005)  # account already holds 0.005 BTC dust
    t = LiveTrader(DrillStrategy(), stage="live", initial_cash=100.0, risk=RISK,
                   exchange=ex, live_dir=tmp_path)
    assert t.state["baseline_base"] == pytest.approx(0.005)
    qty, _ = t._account_raw()
    assert qty == 0.0


def test_cash_cap_hides_excess_wallet_funds(tmp_path):
    ex = FakeExchange(usdt=100.0)
    t = LiveTrader(DrillStrategy(), stage="live", initial_cash=50.0, risk=RISK,
                   exchange=ex, live_dir=tmp_path, cash_cap=50.0)
    _, free_quote = t._account_raw()
    assert free_quote == pytest.approx(50.0)  # wallet has 100, strategy sees only 50


def test_timestamp_error_triggers_clock_resync():
    from execution.live import is_auth_error, resync_clock_if_needed

    calls = []

    class Ex:
        def load_time_difference(self):
            calls.append(1)

    assert resync_clock_if_needed(Ex(), Exception('{"code":-1021,"msg":"Timestamp ahead"}'))
    assert calls == [1]
    assert not resync_clock_if_needed(Ex(), Exception("network timeout"))
    assert calls == [1]
    assert is_auth_error(Exception('{"code":-2015}'))
    assert not is_auth_error(Exception("-1021"))


def test_stage_and_whitelist_required(tmp_path):
    from execution.halt import TraderHalted
    with pytest.raises(TraderHalted, match="stage"):
        LiveTrader(DrillStrategy(), initial_cash=100.0, risk=RISK,
                   exchange=FakeExchange(), live_dir=tmp_path)
    with pytest.raises(TraderHalted, match="allowed_symbols"):
        LiveTrader(DrillStrategy(), stage="live", initial_cash=100.0,
                   risk=RiskConfig(max_order_notional=50), exchange=FakeExchange(),
                   live_dir=tmp_path)


def test_kill_residual_raises_critical_alert(trader, monkeypatch):
    alerts = []
    monkeypatch.setattr("execution.live.send_alert", lambda m: alerts.append(m))
    trader.bot.queue = [[Signal(Side.BUY, 0.3)]]
    trader.step(make_bar(1, 100.0))
    trader.exchange.price = 40.0
    real_create = trader.exchange.create_order

    def half_fill(symbol, type_, side, qty, price=None, params=None):  # liquidation half fills
        return real_create(symbol, type_, side, float(qty) / 2, price, params)
    trader.exchange.create_order = half_fill
    with pytest.raises(SystemExit, match="FLATTEN INCOMPLETE"):
        trader.step(make_bar(2, 40.0))
    assert trader.state["position_qty"] == pytest.approx(0.15)
    assert any(a.startswith("[CRITICAL]") and "residual" in a for a in alerts)
    assert (trader.trade_log.events_df()["kind"] == "flatten_incomplete").any()


def test_auth_alert_masks_ip(tmp_path, monkeypatch):
    import execution.live as live_mod
    alerts = []
    monkeypatch.setattr(live_mod, "_outbound_ip", lambda: "203.0.113.7")
    monkeypatch.setattr(live_mod, "send_alert", lambda m: alerts.append(m))
    ex = FakeExchange()

    def boom():
        raise Exception('{"code":-2015,"msg":"Invalid API-key"}')
    ex.sapi_get_account_apirestrictions = boom
    with pytest.raises(SystemExit):
        LiveTrader(DrillStrategy(), stage="live", initial_cash=100.0, risk=RISK,
                   exchange=ex, live_dir=tmp_path)
    assert alerts and "203.0.x.x" in alerts[0] and "203.0.113.7" not in alerts[0]
