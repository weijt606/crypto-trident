"""Strategy behavior tests (SmaCross example)."""
import pytest

from conftest import HOUR_MS, T0, make_account, make_bar
from core.models import Side
from strategies import build_strategy
from strategies.sma_cross import SmaCross


def feed(bot, closes, account_fn):
    """Feed closes one by one and collect all signals as (i, signal)."""
    out = []
    for i, c in enumerate(closes):
        for sig in bot.on_bar(make_bar(T0 + i * HOUR_MS, c), account_fn(i, c)):
            out.append((i, sig))
    return out


def test_sma_golden_cross_buys_then_death_cross_sells():
    bot = SmaCross("t", "BTC/USDT", "1h", {"fast": 2, "slow": 4, "alloc": 0.95})
    # fall into fast<slow, rise into a golden cross, then fall into a death cross
    closes = [100, 98, 96, 94, 92, 90, 100, 110, 120, 110, 95, 85, 75]
    holding = {"qty": 0.0}

    def account(i, c):
        return make_account(cash=300 if holding["qty"] == 0 else 0,
                            qty=holding["qty"], price=c)

    signals = []
    for i, c in enumerate(closes):
        for sig in bot.on_bar(make_bar(T0 + i * HOUR_MS, c), account(i, c)):
            signals.append((i, sig))
            holding["qty"] = sig.qty if sig.side == Side.BUY else 0.0

    sides = [s.side for _, s in signals]
    assert sides == [Side.BUY, Side.SELL]
    assert signals[0][1].qty > 0


def test_sma_buy_size_uses_alloc():
    bot = SmaCross("t", "BTC/USDT", "1h", {"fast": 2, "slow": 4, "alloc": 0.5})
    closes = [100, 98, 96, 94, 92, 90, 100, 110]
    signals = feed(bot, closes, lambda i, c: make_account(cash=300, price=c))
    assert signals and signals[0][1].side == Side.BUY
    i, sig = signals[0]
    assert sig.qty == pytest.approx(300 * 0.5 / closes[i])


def test_sma_no_signal_during_warmup():
    bot = SmaCross("t", "BTC/USDT", "1h", {"fast": 2, "slow": 4})
    signals = feed(bot, [100, 101, 102], lambda i, c: make_account(price=c))
    assert signals == []
    assert bot.warmup == 4


def test_sma_never_sells_when_flat():
    bot = SmaCross("t", "BTC/USDT", "1h", {"fast": 2, "slow": 4})
    closes = [100, 104, 108, 112, 116, 110, 100, 90, 80]
    signals = feed(bot, closes, lambda i, c: make_account(qty=0, price=c))
    assert all(s.side != Side.SELL for _, s in signals)


def test_sma_rejects_fast_ge_slow():
    with pytest.raises(ValueError):
        SmaCross("t", "BTC/USDT", "1h", {"fast": 10, "slow": 10})


def test_build_strategy_from_config():
    bot = build_strategy({"name": "sma-demo", "strategy": "sma_cross", "symbol": "BTC/USDT",
                          "timeframe": "1h", "params": {"fast": 5, "slow": 10}})
    assert isinstance(bot, SmaCross)
    assert bot.market == "spot"
    assert bot.params["alloc"] == 0.5  # default filled in
    with pytest.raises(KeyError):
        build_strategy({"name": "x", "strategy": "nope", "symbol": "BTC/USDT", "timeframe": "1h"})


def test_sma_defaults_are_classic_golden_cross():
    bot = SmaCross("t", "BTC/USDT", "1h", {})
    assert (bot.params["fast"], bot.params["slow"], bot.params["alloc"]) == (50, 200, 0.5)
    assert bot.warmup == 200
    assert SmaCross.warmup_replay is True


def test_demo_config_matches_defaults():
    from core.config import load_bot_config
    cfg = load_bot_config("sma-demo")
    assert cfg["params"] == SmaCross.default_params()
