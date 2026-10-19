"""Live executor hardening: persisted breakers, single-instance lock, bar-decided-once,
client order ids, fill resolution, exchange-position adoption, flatten bounds, key checks,
startup auth-failure limit, error redaction and bot-name validation (fake exchanges)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import ccxt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from core.models import Bar, Side, Signal  # noqa: E402
from execution.common import CID_MAX_LEN, client_order_id, redact  # noqa: E402
from execution.halt import HALT_EXIT_CODE, TraderHalted, exit_on_halt  # noqa: E402
from execution.live import LiveTrader  # noqa: E402
from execution.live_perp import LivePerpTrader  # noqa: E402
from safety.guard import RiskConfig  # noqa: E402
from test_live_drill import DrillStrategy, FakeExchange, make_bar  # noqa: E402
from test_live_perp import RISK as PERP_RISK  # noqa: E402
from test_live_perp import Bot, FakeFut, bar  # noqa: E402

SPOT_RISK = RiskConfig(max_order_notional=1000, allowed_symbols=["BTC/USDT"])


def spot(tmp_path, ex=None, **kw):
    args = dict(stage="live", initial_cash=100.0, risk=SPOT_RISK,
                exchange=ex or FakeExchange(usdt=100.0), live_dir=tmp_path)
    args.update(kw)
    t = LiveTrader(DrillStrategy(), **args)
    t._fill_poll_s = 0
    return t


def perp(tmp_path, ex=None, **kw):
    args = dict(stage="live", capital=1000.0, fee_bps=5, slippage_bps=0, risk=PERP_RISK,
                max_leverage=3, leverage=2, exchange=ex or FakeFut(wallet=2500.0, price=100.0),
                live_dir=tmp_path)
    args.update(kw)
    t = LivePerpTrader(Bot(), **args)
    t._fill_poll_s = 0
    return t


def halt_bar(h=2, p=100.0):
    return Bar(ts=h * 3600_000, open=p, high=p, low=p, close=p, volume=1,
               symbol="BTC/USDT", timeframe="4h")


# ---- RiskGuard persistence across executor restarts (H1/H2) ----

def test_spot_peak_survives_restart_and_kills(tmp_path):
    ex = FakeExchange(usdt=100.0)
    t = spot(tmp_path, ex)
    ex.price = 100
    t.bot.queue = [[Signal(Side.BUY, 0.9)]]
    t.step(make_bar(1, 100))
    ex.price = 200
    t.bot.queue = [[]]
    t.step(make_bar(30, 200))
    peak = t.guard.peak_equity
    t.close()
    t2 = spot(tmp_path, ex)
    assert t2.guard.peak_equity == pytest.approx(peak)   # not reset to initial_cash
    ex.price = 150                                         # ~19% below the true peak
    t2.bot.queue = [[]]
    with pytest.raises(TraderHalted, match="kill switch"):
        t2.step(make_bar(31, 150))
    assert json.loads(t2.state_file.read_text())["guard"]["killed"] is True


def test_spot_daily_halt_survives_restart(tmp_path):
    ex = FakeExchange(usdt=100.0)
    t = spot(tmp_path, ex)
    ex.price = 100
    t.bot.queue = [[Signal(Side.BUY, 0.8)]]
    t.step(make_bar(1, 100))
    ex.price = 95                                          # ~4% daily loss -> HALT
    t.bot.queue = [[]]
    t.step(make_bar(2, 95))
    assert t.guard.halted_day
    t.close()
    t2 = spot(tmp_path, ex)
    assert t2.guard.halted_day == t.guard.halted_day
    t2.bot.queue = [[Signal(Side.BUY, 0.05)]]
    n = len(ex.orders)
    t2.step(make_bar(3, 95))
    assert len(ex.orders) == n                             # opening still blocked today


def test_perp_daily_halt_survives_restart(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0)
    t = perp(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 15.0)]]
    t.step(bar(1, 100))
    ex.price = 97
    t.bot.queue = [[Signal(Side.BUY, 2.5)]]
    t.step(bar(2, 97))
    assert t.guard.halted_day
    pos = t.portfolio.position_qty
    t.close()
    t2 = perp(tmp_path, ex)
    t2.bot.queue = [[Signal(Side.BUY, 2.5)]]
    t2.step(bar(3, 97))
    assert t2.portfolio.position_qty == pytest.approx(pos)
    assert t2.guard.halted_day == t.guard.halted_day


def test_killed_state_without_kill_file_refuses_restart(tmp_path):
    t = perp(tmp_path)
    t.guard.killed, t.guard.kill_reason = True, "test"
    t._save_state()
    t.close()
    with pytest.raises(TraderHalted, match="active kill"):
        perp(tmp_path)


@pytest.mark.parametrize("legacy", [False, True])
def test_revive_resets_perp_state(tmp_path, legacy):
    from revive import revive_bot
    t = perp(tmp_path)
    t.close()
    s = json.loads(t.state_file.read_text())
    if legacy:  # older perp format: top-level peak_equity, no guard block
        s.pop("guard", None)
        s["peak_equity"] = 5000.0
    else:
        s.setdefault("guard", {}).update(killed=True, kill_reason="dd", peak_equity=5000.0, halted_day="x")
    t.state_file.write_text(json.dumps(s))
    t.kill_file.write_text("ts=1 reason=dd\n")
    revive_bot(tmp_path, "perp-t", "reviewed the drawdown cause, resuming")
    s2 = json.loads(t.state_file.read_text())
    assert s2["guard"]["killed"] is False and s2["guard"]["peak_equity"] is None
    assert "peak_equity" not in s2
    t2 = perp(tmp_path)
    t2.bot.queue = [[]]
    t2.step(bar(1, 100))
    assert t2.guard.peak_equity == pytest.approx(1000.0)   # re-baselined, not 5000


def test_revive_refuses_while_executor_runs(tmp_path):
    from revive import revive_bot
    t = perp(tmp_path)
    t.kill_file.write_text("ts=1 reason=dd\n")
    with pytest.raises(SystemExit, match="executor runs"):
        revive_bot(tmp_path, "perp-t", "reviewed the drawdown cause, resuming")
    assert t.kill_file.exists()


# ---- single-instance lock (M3) ----

@pytest.mark.parametrize("kind", ["spot", "perp"])
def test_second_instance_refused_with_halt_code(tmp_path, kind):
    mk = spot if kind == "spot" else perp
    t = mk(tmp_path)
    with pytest.raises(TraderHalted, match="another process") as ei:
        mk(tmp_path)

    def run():
        raise ei.value
    with pytest.raises(SystemExit) as se:
        exit_on_halt(run)
    assert se.value.code == HALT_EXIT_CODE
    t.close()
    mk(tmp_path)  # lock released -> starts


def test_retire_refuses_while_executor_runs(tmp_path, monkeypatch):
    import retire_live_perp as rl
    running = perp(tmp_path)
    ex = running.exchange
    monkeypatch.setattr(rl, "load_system_config", lambda: {})
    monkeypatch.setattr(rl, "load_bot_config", lambda n: {"market": "perp", "stage": "live"})
    monkeypatch.setattr(rl, "build_perp_trader", lambda s, b, **kw: perp(tmp_path, ex))
    monkeypatch.setattr(sys, "argv", ["retire", "--bot", "perp-t", "--reason", "x", "--yes"])
    with pytest.raises(SystemExit) as se:
        rl.main()
    assert se.value.code == HALT_EXIT_CODE
    assert ex.orders == []


# ---- bar decided once + client order ids (M1/H3) ----

class CountingBot(Bot):
    def __init__(self):
        super().__init__()
        self.seen = []

    def on_bar(self, b, account):
        self.seen.append(b.ts)
        return [Signal(Side.BUY, 2.5, "a"), Signal(Side.BUY, 2.5, "b")]


def test_perp_bar_not_rerun_after_midbar_exception(tmp_path):
    class Flaky(FakeFut):
        n = 0

        def fetch_ticker(self, s):
            Flaky.n += 1
            if Flaky.n == 2:
                raise ccxt.RequestTimeout("timeout")
            return super().fetch_ticker(s)
    ex = Flaky(wallet=2500.0, price=100.0)
    t = perp(tmp_path, ex)
    t.bot = CountingBot()
    with pytest.raises(ccxt.RequestTimeout):
        t.step(bar(1, 100))
    assert t.last_bar_ts == bar(1, 100)[0]
    assert json.loads(t.state_file.read_text())["last_bar_ts"] == bar(1, 100)[0]
    t.step(bar(1, 100))                     # the poll loop retrying the same bar
    assert t.bot.seen == [bar(1, 100)[0]]   # strategy not re-run
    assert len(ex.orders) == 1


def test_perp_orders_carry_deterministic_client_ids(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0)
    t = perp(tmp_path, ex)
    t.bot = CountingBot()
    t.step(bar(1, 100))
    cids = [p["newClientOrderId"] for p in ex.params_log]
    assert cids == [client_order_id("perp-t", 3600_000, "0.0"),
                    client_order_id("perp-t", 3600_000, "1.0")]


def test_spot_orders_carry_client_ids_and_bar_not_rerun(tmp_path):
    ex = FakeExchange(usdt=100.0)
    t = spot(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 0.3)], [Signal(Side.BUY, 0.3)]]
    t.step(make_bar(1, 100))
    t.step(make_bar(1, 100))
    assert len(ex.orders) == 1
    assert ex.params_log[0]["newClientOrderId"] == client_order_id("drill-bot", 3600_000, 0)


def test_client_order_id_rules():
    cid = client_order_id("a very/long bot name with spaces!" * 2, 1_700_000_000_000, "12.3")
    assert len(cid) <= CID_MAX_LEN
    import re
    assert re.fullmatch(r"[.A-Za-z0-9:/_-]{1,36}", cid)
    assert cid.endswith("-1700000000000-12.3")
    assert client_order_id("x" * 40, 1, 0) != client_order_id("x" * 39 + "y", 1, 0)
    assert client_order_id("bot", 5, 1) == "bot-5-1"


def test_spot_timeout_resolved_by_client_id(tmp_path):
    ex = FakeExchange(usdt=100.0)
    placed = {}
    real = ex.create_order

    def create(symbol, type_, side, qty, price=None, params=None):
        resp = real(symbol, type_, side, qty, price, params)
        placed[params["newClientOrderId"]] = dict(resp, status="closed")
        raise ccxt.RequestTimeout("read timed out")
    ex.create_order = create
    ex.fetch_order = lambda oid, sym, params=None: placed[params["origClientOrderId"]]
    t = spot(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 0.3)]]
    t.step(make_bar(1, 100))
    assert len(ex.orders) == 1
    assert t.state["position_qty"] == pytest.approx(0.3)


def test_spot_async_ack_resolved_with_fetch_order(tmp_path):
    ex = FakeExchange(usdt=100.0)
    real = ex.create_order
    full = {}

    def create(symbol, type_, side, qty, price=None, params=None):
        full["r"] = dict(real(symbol, type_, side, qty, price, params), status="closed")
        return {"id": "42", "filled": 0.0, "average": None, "status": "NEW"}
    ex.create_order = create
    ex.fetch_order = lambda oid, sym, params=None: full["r"]
    t = spot(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 0.3)]]
    t.step(make_bar(1, 100))
    assert t.state["position_qty"] == pytest.approx(0.3)
    assert t.state["avg_cost"] > 100


# ---- perp: missed fills are adopted without phantom PnL (H3) ----

class ZeroAck(FakeFut):
    """Fills on the exchange, but the response says nothing filled (and no fetch_order)."""

    def create_order(self, *a, **k):
        super().create_order(*a, **k)
        return {"id": "1", "filled": 0.0, "average": None, "status": "NEW"}


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_perp_missed_fill_adopted_at_mark(tmp_path, side):
    ex = ZeroAck(wallet=2500.0, price=100.0)
    t = perp(tmp_path, ex)
    t.bot.queue = [[Signal(side, 7.5)], []]
    t.step(bar(1, 100))
    assert t.portfolio.position_qty == 0          # nothing booked from an empty ack
    t.step(bar(2, 100))                           # reconcile adopts the exchange position
    assert t.portfolio.position_qty == pytest.approx(ex.pos)
    assert t.portfolio.avg_cost == pytest.approx(100.0)
    assert t.portfolio.equity(100) == pytest.approx(1000.0)   # no phantom loss
    assert not t.guard.killed


def test_perp_adopt_uses_exchange_entry_price(tmp_path):
    class Entry(ZeroAck):
        def fetch_positions(self, syms=None):
            ps = super().fetch_positions(syms)
            for p in ps:
                p["entryPrice"] = 98.0
            return ps
    ex = Entry(wallet=2500.0, price=100.0)
    t = perp(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 5.0)], []]
    t.step(bar(1, 100))
    t.step(bar(2, 100))
    assert t.portfolio.avg_cost == pytest.approx(98.0)
    assert t.portfolio.equity(100) == pytest.approx(1000.0 + 5.0 * 2.0)


def test_perp_async_ack_resolved_with_fetch_order(tmp_path):
    ex = ZeroAck(wallet=2500.0, price=100.0)
    ex.fetch_order = lambda oid, sym, params=None: {"id": oid, "filled": 5.0, "average": 100.5,
                                                   "status": "closed"}
    t = perp(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 5.0)]]
    t.step(bar(1, 100))
    assert t.portfolio.position_qty == pytest.approx(5.0)
    assert t.portfolio.avg_cost > 100.5             # real fill price + fee


def test_perp_books_exchange_fill_beyond_local_cap(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0)
    ex.fill_ratio = 1.3                             # exchange fills more than requested
    t = perp(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 25.0)]]        # 2500 notional; filled 3250 > 3x cap
    t.step(bar(1, 100))
    assert t.portfolio.position_qty == pytest.approx(ex.pos)   # booked, not shrunk
    assert (t.trade_log.events_df()["kind"] == "partial_fill").any()


# ---- perp flatten bounds (M4) ----

def test_flatten_closes_exchange_position_not_stale_local(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0)
    t = perp(tmp_path, ex)
    t.bot.queue = [[Signal(Side.SELL, 5.0)]]
    t.step(bar(1, 100))
    t.portfolio.position_qty = -7.5                 # stale local state
    with pytest.raises(TraderHalted):
        t._soft_cut(halt_bar(), "test")
    assert ex.orders[-1] == ("buy", 5.0)            # capped at the real exchange position
    assert ex.pos == pytest.approx(0.0)


def test_flatten_skips_when_exchange_already_flat(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0)
    t = perp(tmp_path, ex)
    t.portfolio.position_qty = 5.0
    with pytest.raises(TraderHalted):
        t._soft_cut(halt_bar(), "test")
    assert ex.orders == []


def test_flatten_opposite_baseline_sends_plain_order(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0, base_pos=12.5)  # someone else's long
    t = perp(tmp_path, ex)
    t.bot.queue = [[Signal(Side.SELL, 12.5)]]       # bot short: exchange net 0
    t.step(bar(1, 100))
    assert ex.pos == pytest.approx(0.0)
    with pytest.raises(TraderHalted):
        t._soft_cut(halt_bar(), "test")
    assert "reduceOnly" not in ex.params_log[-1]    # reduceOnly would be rejected
    assert ex.orders[-1] == ("buy", 12.5)
    assert ex.pos == pytest.approx(12.5)            # baseline restored


# ---- key checks / auth failures / redaction (L1/L14/L10) ----

@pytest.mark.parametrize("field", ["enableInternalTransfer", "enableMargin", "enableVanillaOptions"])
@pytest.mark.parametrize("kind", ["spot", "perp"])
def test_dangerous_key_permissions_refused(tmp_path, field, kind):
    if kind == "spot":
        ex = FakeExchange()
        base = ex.sapi_get_account_apirestrictions()
    else:
        ex = FakeFut()
        base = ex.sapi_get_account_apirestrictions()
    ex.sapi_get_account_apirestrictions = lambda: dict(base, **{field: "true"})
    with pytest.raises(TraderHalted, match="self-check failed"):
        (spot if kind == "spot" else perp)(tmp_path, ex)


def test_perp_warns_without_ip_whitelist(tmp_path, monkeypatch):
    alerts = []
    monkeypatch.setattr("execution.live_perp.send_alert", lambda m: alerts.append(m))
    perp(tmp_path)
    assert any("no IP whitelist" in a for a in alerts)


def test_startup_auth_failures_halt_after_limit(tmp_path, monkeypatch):
    import execution.live as live_mod
    from execution.common import STARTUP_AUTH_FAIL_LIMIT
    monkeypatch.setattr(live_mod, "send_alert", lambda m: None)
    monkeypatch.setattr(live_mod, "_outbound_ip", lambda: "198.51.100.4")
    ex = FakeFut()
    good = ex.sapi_get_account_apirestrictions

    def boom():
        raise Exception('binance {"code":-2015,"msg":"Invalid API-key"}')
    ex.sapi_get_account_apirestrictions = boom
    for _ in range(STARTUP_AUTH_FAIL_LIMIT - 1):
        with pytest.raises(SystemExit) as ei:
            perp(tmp_path, ex)
        assert not isinstance(ei.value, TraderHalted)
    with pytest.raises(TraderHalted, match="consecutive startup auth failures"):
        perp(tmp_path, ex)
    ex.sapi_get_account_apirestrictions = good
    perp(tmp_path, ex).close()                       # success resets the counter
    assert not (tmp_path / "perp-t.auth_fails").exists()


def test_redact_strips_query_and_signature():
    msg = ('binance GET https://api.binance.com/api/v3/order?symbol=BTCUSDT&timestamp=1&'
           'signature=' + "ab" * 32 + ' 400 {"code":-2010}')
    out = redact(msg)
    assert "signature=ab" not in out and "timestamp=1" not in out
    assert "https://api.binance.com/api/v3/order?<redacted>" in out and "-2010" in out
    assert "ab" * 32 not in redact("signature=" + "ab" * 32)


def test_order_error_alert_is_redacted(tmp_path, monkeypatch):
    alerts = []
    monkeypatch.setattr("execution.live_perp.send_alert", lambda m: alerts.append(m))
    ex = FakeFut(wallet=2500.0, price=100.0)

    def fail(*a, **k):
        raise ccxt.ExchangeError("POST https://fapi.binance.com/fapi/v1/order?x=1&signature="
                                 + "cd" * 32)
    ex.create_order = fail
    t = perp(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 5.0)]]
    t.step(bar(1, 100))
    err = [a for a in alerts if "order failed" in a]
    assert err and "cd" * 32 not in err[0] and "<redacted>" in err[0]
    ev = t.trade_log.events_df()
    assert not ev["detail"].astype(str).str.contains("cd" * 32).any() \
        if "detail" in ev else True


# ---- config validation (L2/L3/M2) ----

def test_spot_drawdown_clamped(tmp_path):
    t = spot(tmp_path, risk=RiskConfig(max_order_notional=1000, allowed_symbols=["BTC/USDT"],
                                       max_drawdown_pct=0.6))
    assert t.guard.cfg.max_drawdown_pct == pytest.approx(0.30)


def test_clamp_drawdown_rejects_nan():
    from execution.live import clamp_drawdown
    for bad in (float("nan"), 0, "abc", None, [0.1]):
        with pytest.raises(TraderHalted, match="max_drawdown_pct"):
            clamp_drawdown(bad)
    assert clamp_drawdown(0.9) == 0.30 and clamp_drawdown(0.1) == 0.1


def test_bot_name_validation(tmp_path, monkeypatch):
    import core.config as cc
    for bad in ("../x", "a b", "x/y", "..", ""):
        with pytest.raises(ValueError):
            cc.load_bot_config(bad)
    (tmp_path / "bots").mkdir()
    (tmp_path / "bots" / "good-bot.yaml").write_text("name: other-bot\n")
    monkeypatch.setattr(cc, "CONFIG_DIR", tmp_path)
    with pytest.raises(ValueError, match="must equal the file name"):
        cc.load_bot_config("good-bot")
    (tmp_path / "bots" / "good-bot.yaml").write_text("name: good-bot\n")
    assert cc.load_bot_config("good-bot")["name"] == "good-bot"


def test_state_writes_leave_no_temp_files(tmp_path):
    t = perp(tmp_path)
    t.bot.queue = [[Signal(Side.BUY, 5.0)]]
    t.step(bar(1, 100))
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert math.isfinite(json.loads(t.state_file.read_text())["cash"])


# ---- spot: base-asset fees, sell caps, dust (H1) ----

class BaseFeeEx(FakeExchange):
    """Binance-like spot: the buyer's commission is deducted in the base asset, the lot
    step is 0.00001 (amounts truncated) and selling beyond the free balance is rejected."""

    def __init__(self, *a, report_fee=True, **kw):
        super().__init__(*a, **kw)
        self.report_fee = report_fee
        self.my_trades = {}

    def amount_to_precision(self, symbol, qty):
        return f"{math.floor(float(qty) * 1e5 + 1e-9) / 1e5:.5f}"

    def create_order(self, symbol, type_, side, qty, price=None, params=None):
        qty = float(qty)
        oid = str(len(self.orders) + 1)
        if side == "buy":
            self.balances["USDT"] -= qty * self.price
            fee = {"cost": qty * 0.001, "currency": self.base}
            self.balances[self.base] += qty - fee["cost"]
        else:
            if qty > self.balances[self.base] + 1e-12:
                raise ccxt.InsufficientFunds("Account has insufficient balance for requested action.")
            self.balances[self.base] -= qty
            fee = {"cost": qty * self.price * 0.001, "currency": "USDT"}
            self.balances["USDT"] += qty * self.price - fee["cost"]
        self.orders.append({"side": side, "qty": qty, "price": self.price})
        self.my_trades[oid] = [{"order": oid, "fee": fee}]
        resp = {"id": oid, "filled": qty, "average": self.price, "status": "closed"}
        if self.report_fee:
            resp["trades"] = [{"fee": fee}]
        return resp


class ExitBot(DrillStrategy):
    """Sells the whole booked position when asked."""
    exit_now = False

    def on_bar(self, bar, account):
        if self.exit_now and account.position_qty > 0:
            return [Signal(Side.SELL, account.position_qty, "exit")]
        return super().on_bar(bar, account)


def test_spot_base_fee_buy_then_full_exit_leaves_no_residual(tmp_path):
    ex = BaseFeeEx(usdt=100.0)
    t = spot(tmp_path, ex)
    t.bot = ExitBot()
    t.bot.queue = [[Signal(Side.BUY, 0.9)]]
    t.step(make_bar(1, 100))
    assert t.state["position_qty"] == pytest.approx(0.9 - 0.0009)   # filled - base fee
    assert t.state["position_qty"] == pytest.approx(ex.balances["BTC"])
    t.bot.exit_now = True
    t.step(make_bar(2, 100))
    assert [o["side"] for o in ex.orders] == ["buy", "sell"]
    assert ex.balances["BTC"] * 100 < SPOT_RISK.min_order_notional  # at most dust left
    assert t.state["position_qty"] == 0.0
    assert not (t.trade_log.events_df()["kind"] == "order_error").any()
    t.bot.exit_now = False
    t.step(make_bar(3, 100))                       # dust does not raise a reconcile alert
    assert not (t.trade_log.events_df()["kind"] == "reconcile").any()


def test_spot_kill_with_base_fee_sells_exchange_balance(tmp_path):
    ex = BaseFeeEx(usdt=100.0)
    t = spot(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 0.9)]]
    t.step(make_bar(1, 100))
    t.state["position_qty"] = 0.9                  # stale books claim more than exists
    t._save_state()
    ex.price = 80
    with pytest.raises(TraderHalted) as ei:
        t.step(make_bar(2, 80))
    assert "INCOMPLETE" not in ei.value.message and "UNVERIFIED" not in ei.value.message
    assert ex.orders[-1]["side"] == "sell"
    assert ex.balances["BTC"] * 80 < SPOT_RISK.min_order_notional
    assert t.state["position_qty"] == 0.0


def test_spot_sell_capped_at_free_balance_minus_baseline(tmp_path):
    ex = BaseFeeEx(usdt=100.0, dust=0.002)        # pre-existing baseline
    t = spot(tmp_path, ex)
    t.bot = ExitBot()
    t.bot.queue = [[Signal(Side.BUY, 0.5)]]
    t.step(make_bar(1, 100))
    t.state["position_qty"] = 0.6                  # books overstate the position
    t._save_state()
    t.bot.exit_now = True
    t.step(make_bar(2, 100))
    assert ex.orders[-1]["side"] == "sell"
    assert ex.balances["BTC"] == pytest.approx(0.002, abs=2e-5)  # baseline untouched


def test_spot_fee_fetched_via_trades_when_response_has_none(tmp_path):
    ex = BaseFeeEx(usdt=100.0, report_fee=False)
    ex.fetch_my_trades = lambda sym, params=None: ex.my_trades[params["orderId"]]
    t = spot(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 0.5)]]
    t.step(make_bar(1, 100))
    assert t.state["position_qty"] == pytest.approx(0.5 - 0.0005)
    assert t.state["fees_paid"] == pytest.approx(0.0005 * 100)


def test_spot_fee_estimated_from_fee_bps_without_trades(tmp_path, caplog):
    ex = BaseFeeEx(usdt=100.0, report_fee=False)
    t = spot(tmp_path, ex, fee_bps=10)
    t.bot.queue = [[Signal(Side.BUY, 0.5)]]
    with caplog.at_level("WARNING"):
        t.step(make_bar(1, 100))
    assert t.state["fees_paid"] == pytest.approx(0.5 * 100 * 10 / 10_000)
    assert any("estimating" in r.getMessage() for r in caplog.records)


# ---- non-finite exchange data never reaches the books (L5) ----

def test_spot_nonfinite_balance_never_written(tmp_path):
    ex = FakeExchange(usdt=100.0)
    t = spot(tmp_path, ex)
    t.step(make_bar(1, 100))
    ex.balances["BTC"] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        t.step(make_bar(2, 100))
    st = json.loads(t.state_file.read_text())
    assert st["position_qty"] == 0.0


def test_spot_nonfinite_fee_ignored(tmp_path):
    t = spot(tmp_path)
    fee, base_fee = t._fee_to_quote([{"cost": float("nan"), "currency": "USDT"},
                                     {"cost": 0.1, "currency": "USDT"}], 100.0)
    assert fee == pytest.approx(0.1) and base_fee == 0.0


def test_perp_nonfinite_funding_skipped(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0)
    t = perp(tmp_path, ex)
    t.state["funding_synced_ts"] = 1
    ex.fetch_funding_history = lambda sym, since=None: [
        {"amount": float("nan"), "timestamp": 10}, {"amount": "1.5", "timestamp": 20}]
    t._sync_funding(30)
    assert t.portfolio.cash == pytest.approx(1001.5)
    assert t.state["funding_synced_ts"] == 20
    assert (t.trade_log.events_df()["kind"] == "funding_error").any()
    t._save_state()                                # strict JSON: would raise on NaN


def test_perp_nonfinite_fill_price_not_booked(tmp_path):
    class NanFut(FakeFut):
        def create_order(self, *a, **kw):
            r = super().create_order(*a, **kw)
            return dict(r, average=float("inf"), status="closed")
        def fetch_ticker(self, sym):
            return {"last": float("nan")}
    ex = NanFut(wallet=2500.0, price=100.0)
    t = perp(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 2.0)]]
    t.step(bar(1, 100))
    assert t.portfolio.position_qty == 0.0         # not booked; next bar reconciles
    assert math.isfinite(t.portfolio.cash)
    json.loads(t.state_file.read_text())


# ---- perp flatten: baseline closed outside the bot (L1) ----

@pytest.mark.parametrize("left", [0.0, 4.0])
def test_flatten_baseline_closed_externally_sends_nothing(tmp_path, left):
    ex = FakeFut(wallet=2500.0, price=100.0, base_pos=12.5)  # someone else's long
    t = perp(tmp_path, ex)
    assert t._baseline_perp == pytest.approx(12.5)
    ex.pos = left                                   # user closed (part of) it by hand
    with pytest.raises(TraderHalted, match="BASELINE CHANGED"):
        t._soft_cut(halt_bar(), "test")
    assert ex.orders == []
    assert ex.pos == pytest.approx(left)
    assert t.portfolio.position_qty == 0.0
    assert t._baseline_perp == pytest.approx(left)
    assert (t.trade_log.events_df()["kind"] == "baseline_changed").any()


def test_step_baseline_closed_externally_no_phantom_position(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0, base_pos=12.5)
    t = perp(tmp_path, ex)
    ex.pos = 0.0
    t.step(bar(1, 100))
    assert t.portfolio.position_qty == 0.0 and t._baseline_perp == 0.0
    with pytest.raises(TraderHalted):
        t._soft_cut(halt_bar(), "test")
    assert ex.orders == []


def test_flatten_reruns_use_fresh_client_ids(tmp_path):
    ex = FakeFut(wallet=2500.0, price=100.0)
    ex.ignore_reduce_only = True
    t = perp(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 5.0)]]
    t.step(bar(1, 100))
    ex.fill_ratio = 0.5                             # first flatten leaves a residual
    t._flatten_and_persist(halt_bar(), "retire")
    t._flatten_and_persist(halt_bar(), "retire")    # re-run on the same bar
    cids = [p["newClientOrderId"] for p in ex.params_log[1:]]
    assert len(cids) == 2 and len(set(cids)) == 2
    assert t.state["flatten_attempt"] == 2


def test_retire_dry_run_changes_no_exchange_setting(tmp_path, monkeypatch, capsys):
    import retire_live_perp as rl

    class Watch(FakeFut):
        margin_calls = 0
        def set_margin_mode(self, mode, sym):
            Watch.margin_calls += 1
            return {}
    ex = Watch(wallet=2500.0, price=100.0)
    monkeypatch.setattr(rl, "load_system_config", lambda: {})
    monkeypatch.setattr(rl, "load_bot_config", lambda n: {"market": "perp", "stage": "live"})
    monkeypatch.setattr(rl, "build_perp_trader",
                        lambda s, b, dry_run=False: perp(tmp_path, ex, dry_run=dry_run))
    monkeypatch.setattr(rl, "fetch_latest_closed_bar",
                        lambda *a, **k: [3600_000, 100, 100, 100, 100, 1])
    monkeypatch.setattr(sys, "argv", ["retire", "--bot", "perp-t", "--reason", "x"])
    rl.main()
    assert "dry run" in capsys.readouterr().out
    assert Watch.margin_calls == 0 and ex.leverage_set is None and ex.orders == []


# ---- revive re-baselines the daily-loss reference (L2) ----

def test_same_day_revive_does_not_retrip_halt(tmp_path):
    ex = FakeExchange(usdt=100.0)
    t = spot(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 0.9)]]
    t.step(make_bar(1, 100))
    ex.price = 80
    with pytest.raises(TraderHalted):
        t.step(make_bar(2, 80))                     # kill (and daily HALT) on this day
    from revive import revive_bot
    revive_bot(tmp_path, "drill-bot", "reviewed: test drawdown, safe to resume")
    st = json.loads(t.state_file.read_text())
    assert st["guard"]["day_start_equity"] is None and st["guard"]["current_day"] == ""
    t2 = spot(tmp_path, ex)
    t2.step(make_bar(3, 80))                        # same UTC day, same equity
    assert t2.guard.halted_day == "" and not t2.guard.killed


# ---- spot kill retry nonce + no double booking (audit L3) ----

def test_spot_kill_client_id_carries_persisted_retry_counter(tmp_path):
    ex = FakeExchange(usdt=100.0)
    t = spot(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 0.9)]]
    t.step(make_bar(1, 100))
    t.state["kill_attempt"] = 1          # an earlier kill attempt on this bar already ran
    ex.price = 80
    with pytest.raises(TraderHalted, match="kill switch"):
        t.step(make_bar(2, 80))
    cid = ex.params_log[-1]["newClientOrderId"]
    assert cid.endswith("-kill2")
    assert json.loads(t.state_file.read_text())["kill_attempt"] == 2


def test_spot_lookup_of_already_booked_order_is_not_booked_twice(tmp_path):
    class Lookup(FakeExchange):
        timeout_next = False
        last = None

        def create_order(self, symbol, type_, side, qty, price=None, params=None):
            if self.timeout_next:
                raise ccxt.RequestTimeout("timed out")
            self.last = super().create_order(symbol, type_, side, qty, price, params)
            self.last["status"] = "closed"
            return self.last

        def fetch_order(self, oid, symbol, params=None):
            return dict(self.last)   # lookup resolves to the order booked earlier

    ex = Lookup(usdt=100.0)
    t = spot(tmp_path, ex)
    t.bot.queue = [[Signal(Side.BUY, 0.5)]]
    t.step(make_bar(1, 100))
    pos = t.state["position_qty"]
    assert t.state["booked_order_ids"] == [ex.last["id"]]
    ex.timeout_next = True
    t.bot.queue = [[Signal(Side.BUY, 0.1)]]
    t.step(make_bar(2, 100))
    assert t.state["position_qty"] == pytest.approx(pos)
    assert len(t.trade_log.fills_df()) == 1


# ---- numeric live config validation (audit L1) ----

@pytest.mark.parametrize("kw", [dict(initial_cash=0), dict(initial_cash=float("nan")),
                                dict(initial_cash="abc"), dict(cash_cap=-1),
                                dict(cash_cap=float("inf"))])
def test_spot_rejects_bad_numeric_config(tmp_path, kw):
    with pytest.raises(TraderHalted, match="must be"):
        spot(tmp_path, **kw)


@pytest.mark.parametrize("kw", [dict(capital=0), dict(capital=float("nan")),
                                dict(leverage=0), dict(leverage=float("nan")),
                                dict(max_leverage=-1), dict(soft_cut_pct=0),
                                dict(soft_cut_pct=1.0), dict(soft_cut_pct=float("nan")),
                                dict(max_consec_losses=0), dict(max_consec_losses=2.5),
                                dict(max_consec_losses=float("inf"))])
def test_perp_rejects_bad_numeric_config(tmp_path, kw):
    with pytest.raises(TraderHalted, match="must be") as ei:
        perp(tmp_path, **kw)

    def run():
        raise ei.value
    with pytest.raises(SystemExit) as se:
        exit_on_halt(run)
    assert se.value.code == HALT_EXIT_CODE


def test_perp_accepts_valid_soft_cut_config(tmp_path):
    t = perp(tmp_path, soft_cut_pct=0.1, max_consec_losses=3)
    assert t.soft_cut_pct == 0.1 and t.max_consec_losses == 3


@pytest.mark.parametrize("bot_cfg,sys_live", [
    ({"cash_cap": 0}, {}), ({}, {"cash_cap": float("nan")}), ({}, {}),
    ({"cash_cap": 100, "params": {"lev": "x"}}, {}),
])
def test_build_perp_trader_rejects_bad_numeric_config(bot_cfg, sys_live):
    from run_live_perp import build_perp_trader
    sys_cfg = {"risk": {}, "live": sys_live, "fees": {}, "slippage_bps": 0}
    cfg = {"name": "perp-t", "market": "perp", "stage": "live", **bot_cfg}
    with pytest.raises(TraderHalted, match="must be"):
        build_perp_trader(sys_cfg, cfg)


# ---- redaction + transient network errors at startup (audit L2) ----

def test_exit_on_halt_turns_network_error_into_redacted_exit_1(capsys):
    def run():
        raise ccxt.RequestTimeout(
            "binance GET https://api.binance.com/api/v3/account?timestamp=1&signature=abc123")
    with pytest.raises(SystemExit) as se:
        exit_on_halt(run)
    assert se.value.code == 1
    err = capsys.readouterr().err
    assert "abc123" not in err and "RequestTimeout" in err


def test_perp_margin_halt_message_is_redacted(tmp_path):
    class Reject(FakeFut):
        def set_leverage(self, lev, sym):
            raise ccxt.ExchangeError(
                "binance POST https://fapi.binance.com/fapi/v1/leverage?signature=deadbeef")
    with pytest.raises(TraderHalted) as ei:
        perp(tmp_path, Reject(wallet=2500.0, price=100.0))
    assert "deadbeef" not in str(ei.value) and "could not set leverage" in str(ei.value)


def test_verify_trade_key_redacts_auth_error(monkeypatch, capsys):
    import verify_trade_key as vk

    class Ex:
        def __init__(self, *a, **k):
            pass

        def sapi_get_account_apirestrictions(self):
            raise ccxt.AuthenticationError(
                "binance GET https://api.binance.com/sapi/v1/account/apiRestrictions?"
                "timestamp=1&signature=feedface")
    monkeypatch.setattr(vk, "trade_keys", lambda: ("k", "s"))
    monkeypatch.setattr(vk.ccxt, "binance", Ex)
    monkeypatch.setattr(sys, "argv", ["verify"])
    assert vk.main() == 1
    out = capsys.readouterr().out
    assert "feedface" not in out and "authentication failed" in out
