"""Risk guard tests: every line of defense must actually block."""
import pytest

from conftest import HOUR_MS, T0
from core.models import Order, Side
from safety.guard import RiskConfig, RiskGuard

DAY_MS = 86_400_000


def order(side=Side.BUY, qty=1.0, ts=T0):
    return Order(ts=ts, symbol="BTC/USDT", side=side, qty=qty, bot="test")


def fresh_guard(**overrides) -> RiskGuard:
    cfg = RiskConfig(**overrides)
    return RiskGuard(cfg, initial_equity=1000.0)


def test_min_notional_rejected():
    g = fresh_guard()
    v = g.check_order(order(qty=0.01), price=100, account_equity=1000, position_qty=0)
    assert not v.ok and "min order notional" in v.reason


def test_max_notional_rejected():
    g = fresh_guard(max_order_notional=50)
    v = g.check_order(order(qty=1.0), price=100, account_equity=1000, position_qty=0)
    assert not v.ok and "exceeds limit" in v.reason


def test_position_pct_limit():
    g = fresh_guard(max_position_pct=0.15)
    # holding 1 @100, buying 1 more -> 200/1000 = 20% > 15%
    v = g.check_order(order(qty=1.0), price=100, account_equity=1000, position_qty=1.0)
    assert not v.ok and "position notional" in v.reason
    # sells are not bound by the position cap
    v = g.check_order(order(side=Side.SELL, qty=1.0), price=100,
                      account_equity=1000, position_qty=1.0)
    assert v.ok


def test_no_short_selling():
    g = fresh_guard()
    v = g.check_order(order(side=Side.SELL, qty=2.0), price=100,
                      account_equity=1000, position_qty=1.0)
    assert not v.ok and "short selling" in v.reason


def test_rate_limit():
    g = fresh_guard(max_orders_per_minute=3)
    for i in range(3):
        v = g.check_order(order(qty=0.1, ts=T0 + i * 1000), price=100,
                          account_equity=1000, position_qty=0)
        assert v.ok
    v = g.check_order(order(qty=0.1, ts=T0 + 3000), price=100,
                      account_equity=1000, position_qty=0)
    assert not v.ok and "rate limit" in v.reason
    # window slides after one minute, orders allowed again
    v = g.check_order(order(qty=0.1, ts=T0 + 61_000), price=100,
                      account_equity=1000, position_qty=0)
    assert v.ok


def test_daily_loss_halts_buys_allows_sells():
    g = fresh_guard(daily_loss_pct=0.03)
    g.on_mark(T0, 1000.0)
    events = g.on_mark(T0 + HOUR_MS, 965.0)  # -3.5% today
    assert any("HALT" in e for e in events)

    ts = T0 + 2 * HOUR_MS
    v = g.check_order(order(qty=0.5, ts=ts), price=100, account_equity=965, position_qty=0)
    assert not v.ok and "opening blocked" in v.reason
    v = g.check_order(order(side=Side.SELL, qty=0.5, ts=ts), price=100,
                      account_equity=965, position_qty=1.0)
    assert v.ok  # reducing allowed


def test_daily_halt_recovers_next_day():
    g = fresh_guard(daily_loss_pct=0.03)
    g.on_mark(T0, 1000.0)
    g.on_mark(T0 + HOUR_MS, 965.0)
    next_day = T0 + DAY_MS
    g.on_mark(next_day, 965.0)  # next day resets the daily baseline
    v = g.check_order(order(qty=0.5, ts=next_day + 1000), price=100,
                      account_equity=965, position_qty=0)
    assert v.ok


def test_drawdown_kill_and_revive():
    g = fresh_guard(max_drawdown_pct=0.15)
    g.on_mark(T0, 1000.0)
    g.on_mark(T0 + HOUR_MS, 1100.0)  # new peak
    events = g.on_mark(T0 + 2 * HOUR_MS, 930.0)  # -15.5% from peak
    assert g.killed and any("KILL" in e for e in events)

    # after kill, both buys and sells are rejected
    for side in (Side.BUY, Side.SELL):
        v = g.check_order(order(side=side, qty=0.5, ts=T0 + 3 * HOUR_MS),
                          price=100, account_equity=930, position_qty=1.0)
        assert not v.ok and "kill switch" in v.reason

    g.revive()
    assert not g.killed
    v = g.check_order(order(qty=0.5, ts=T0 + 4 * HOUR_MS), price=100,
                      account_equity=930, position_qty=0)
    assert v.ok


def test_kill_fires_only_once():
    g = fresh_guard(max_drawdown_pct=0.15)
    g.on_mark(T0, 1000.0)
    assert any("KILL" in e for e in g.on_mark(T0 + HOUR_MS, 600.0))
    assert g.on_mark(T0 + 2 * HOUR_MS, 550.0) == []  # reported only once


def test_symbol_whitelist():
    g = fresh_guard(allowed_symbols=["BTC/USDT", "ETH/USDT"])
    v = g.check_order(Order(ts=T0, symbol="DOGE/USDT", side=Side.BUY, qty=1.0, bot="t"),
                      price=10, account_equity=1000, position_qty=0)
    assert not v.ok and "whitelist" in v.reason
    v = g.check_order(Order(ts=T0, symbol="BTC/USDT", side=Side.BUY, qty=0.001, bot="t"),
                      price=60000, account_equity=1000, position_qty=0)
    assert v.ok
    # default None = unrestricted
    g2 = fresh_guard()
    v = g2.check_order(Order(ts=T0, symbol="DOGE/USDT", side=Side.BUY, qty=1.0, bot="t"),
                       price=10, account_equity=1000, position_qty=0)
    assert v.ok


def test_daily_loss_not_triggered_within_threshold():
    g = fresh_guard(daily_loss_pct=0.03)
    g.on_mark(T0, 1000.0)
    assert g.on_mark(T0 + HOUR_MS, 985.0) == []  # -1.5%, below threshold


def _halted_guard(**overrides) -> RiskGuard:
    g = fresh_guard(daily_loss_pct=0.03, **overrides)
    g.on_mark(T0, 1000.0)
    assert any("HALT" in e for e in g.on_mark(T0 + HOUR_MS, 965.0))
    return g


def test_halt_blocks_opening_and_adding_short():
    g = _halted_guard(allow_short=True, max_leverage=3)
    ts = T0 + 2 * HOUR_MS
    v = g.check_order(order(side=Side.SELL, qty=0.5, ts=ts), price=100,
                      account_equity=965, position_qty=0)
    assert not v.ok and "opening blocked" in v.reason          # open short
    v = g.check_order(order(side=Side.SELL, qty=0.5, ts=ts), price=100,
                      account_equity=965, position_qty=-1.0)
    assert not v.ok and "opening blocked" in v.reason          # add to short
    v = g.check_order(order(side=Side.SELL, qty=3.0, ts=ts), price=100,
                      account_equity=965, position_qty=1.0)
    assert not v.ok and "opening blocked" in v.reason          # flip long 1 -> short 2


def test_halt_allows_covering_short():
    g = _halted_guard(allow_short=True, max_leverage=3)
    ts = T0 + 2 * HOUR_MS
    v = g.check_order(order(side=Side.BUY, qty=0.5, ts=ts), price=100,
                      account_equity=965, position_qty=-1.0)
    assert v.ok                                                # partial cover
    v = g.check_order(order(side=Side.BUY, qty=1.0, ts=ts + 1000), price=100,
                      account_equity=965, position_qty=-1.0)
    assert v.ok                                                # full cover


def test_halt_spot_behavior_unchanged():
    g = _halted_guard()
    ts = T0 + 2 * HOUR_MS
    v = g.check_order(order(qty=0.5, ts=ts), price=100, account_equity=965, position_qty=0)
    assert not v.ok and "opening blocked" in v.reason          # spot buy (open)
    v = g.check_order(order(qty=0.5, ts=ts), price=100, account_equity=965, position_qty=1.0)
    assert not v.ok and "opening blocked" in v.reason          # spot buy (add)
    v = g.check_order(order(side=Side.SELL, qty=1.0, ts=ts), price=100,
                      account_equity=965, position_qty=1.0)
    assert v.ok                                                # spot sell (reduce)
    v = g.check_order(order(side=Side.SELL, qty=2.0, ts=ts + 1000), price=100,
                      account_equity=965, position_qty=1.0)
    assert not v.ok                                            # spot still cannot short/flip


def test_halt_blocks_equal_size_flip_allows_partial_reduce():
    g = _halted_guard(allow_short=True, max_leverage=3)
    ts = T0 + 2 * HOUR_MS
    v = g.check_order(order(side=Side.SELL, qty=2.0, ts=ts), price=100,
                      account_equity=965, position_qty=1.0)
    assert not v.ok and "opening blocked" in v.reason          # long 1 -> short 1
    v = g.check_order(order(side=Side.BUY, qty=2.0, ts=ts), price=100,
                      account_equity=965, position_qty=-1.0)
    assert not v.ok and "opening blocked" in v.reason          # short 1 -> long 1
    v = g.check_order(order(side=Side.SELL, qty=0.5, ts=ts), price=100,
                      account_equity=965, position_qty=1.0)
    assert v.ok                                                # partial long reduce


def test_position_cap_applies_to_equal_size_flip():
    # Cap = 0.5 x 1 = 50% of equity; flipping long 6 -> short 6 opens 600/1000.
    g = fresh_guard(allow_short=True, max_position_pct=0.5, max_order_notional=5000)
    v = g.check_order(order(side=Side.SELL, qty=12.0), price=100,
                      account_equity=1000, position_qty=6.0)
    assert not v.ok and "exceeds limit" in v.reason
    v = g.check_order(order(side=Side.SELL, qty=3.0), price=100,
                      account_equity=1000, position_qty=6.0)
    assert v.ok                                                # pure reduction


# ---- bad numbers fail closed ----

NAN, INF = float("nan"), float("inf")


@pytest.mark.parametrize("kw", [
    dict(max_drawdown_pct=NAN), dict(max_drawdown_pct=0), dict(max_drawdown_pct=1.5),
    dict(daily_loss_pct=-0.1), dict(max_position_pct=0), dict(max_position_pct=1.2),
    dict(max_leverage=0.5), dict(max_leverage=NAN), dict(max_order_notional=0),
    dict(max_order_notional=INF), dict(min_order_notional=-1), dict(max_orders_per_minute=0),
    dict(max_orders_per_minute=2.5), dict(min_order_notional=2000, max_order_notional=1000),
])
def test_risk_config_rejects_invalid_values(kw):
    with pytest.raises(ValueError):
        RiskConfig(**kw)


@pytest.mark.parametrize("qty,price,equity,pos", [
    (NAN, 100, 1000, 0), (1, NAN, 1000, 0), (1, INF, 1000, 0), (-5, 100, 1000, 0),
    (0, 100, 1000, 0), (1, 0, 1000, 0), (1, 100, NAN, 0), (1, 100, 1000, NAN),
])
def test_non_finite_or_non_positive_inputs_rejected(qty, price, equity, pos):
    g = fresh_guard(max_order_notional=1e9)
    v = g.check_order(order(qty=qty), price=price, account_equity=equity, position_qty=pos)
    assert not v.ok


@pytest.mark.parametrize("equity", [0.0, -50.0])
def test_opening_blocked_when_equity_not_positive(equity):
    g = fresh_guard()
    v = g.check_order(order(qty=0.5), price=100, account_equity=equity, position_qty=0)
    assert not v.ok and "equity" in v.reason
    v = g.check_order(order(side=Side.SELL, qty=0.5), price=100,
                      account_equity=equity, position_qty=1.0)
    assert v.ok                                                # reducing still allowed


def test_nan_mark_ignored_and_does_not_mask_breakers():
    g = fresh_guard(max_drawdown_pct=0.15)
    g.on_mark(T0, 1000)
    ev = g.on_mark(T0 + HOUR_MS, NAN)
    assert ev and "ignored" in ev[0]
    assert g.peak_equity == 1000 and not g.killed
    assert g.on_mark(T0 + 2 * HOUR_MS, 500)                    # real drop still kills
    assert g.killed
    g.on_mark(T0 + 3 * HOUR_MS, NAN)
    assert g.killed                                            # NaN never clears a kill


def test_export_load_roundtrip_keeps_halt_and_kill():
    g = fresh_guard(daily_loss_pct=0.03)
    g.on_mark(T0, 1000)
    g.on_mark(T0 + HOUR_MS, 960)
    assert g.halted_day
    g2 = fresh_guard(daily_loss_pct=0.03)
    g2.load_state(g.export_state())
    v = g2.check_order(order(qty=1.0, ts=T0 + 2 * HOUR_MS), price=100,
                       account_equity=960, position_qty=0)
    assert not v.ok and "opening blocked" in v.reason
    g2.on_mark(T0 + 2 * HOUR_MS, 960)                          # same day: no new day start
    assert g2.day_start_equity == 1000
    g.killed, g.kill_reason = True, "dd"
    g3 = fresh_guard()
    g3.load_state(g.export_state())
    assert g3.killed and not g3.check_order(order(), 100, 1000, 0).ok


def test_load_state_peak_none_rebases_at_next_mark():
    g = fresh_guard()
    g.load_state({"peak_equity": None, "killed": False})
    g.on_mark(T0, 700)                                         # would be a 30% drawdown
    assert not g.killed and g.peak_equity == 700


def test_load_state_ignores_bad_numbers():
    g = fresh_guard()
    g.load_state({"peak_equity": "nan", "day_start_equity": None})
    assert g.peak_equity == 1000 and g.day_start_equity == 1000
