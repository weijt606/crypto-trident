"""Live launcher gates: stage refusal exits as a halt; startup auth alerts are throttled."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from execution.halt import HALT_EXIT_CODE  # noqa: E402


@pytest.mark.parametrize("script,market", [("run_live", "spot"), ("run_live_perp", "perp")])
@pytest.mark.parametrize("stage", [None, "paper"])
def test_stage_gate_exits_as_halt(monkeypatch, script, market, stage):
    mod = importlib.import_module(script)
    monkeypatch.setattr(mod, "load_system_config", lambda: {})
    monkeypatch.setattr(mod, "load_bot_config", lambda name: {"market": market, "stage": stage})
    monkeypatch.setattr(sys, "argv", [script, "--bot", "x"])
    with pytest.raises(SystemExit) as ei:
        mod.main()
    assert ei.value.code == HALT_EXIT_CODE


def test_startup_auth_alert_rate_limited(tmp_path, monkeypatch):
    import execution.live as live_mod
    sent = []
    monkeypatch.setattr(live_mod, "send_alert", lambda m: sent.append(m))
    clock = [1_000_000.0]
    monkeypatch.setattr(live_mod.time, "time", lambda: clock[0])
    assert live_mod.send_startup_auth_alert(tmp_path, "b", "auth failed")
    clock[0] += 600
    assert not live_mod.send_startup_auth_alert(tmp_path, "b", "auth failed")
    assert live_mod.send_startup_auth_alert(tmp_path, "other", "auth failed")  # per bot
    clock[0] += 3600
    assert live_mod.send_startup_auth_alert(tmp_path, "b", "auth failed")
    assert len(sent) == 3


def test_repeated_startup_auth_failure_alerts_once(tmp_path, monkeypatch):
    import execution.live as live_mod
    from execution.live_perp import LivePerpTrader
    from safety.guard import RiskConfig
    from test_live_perp import Bot, FakeFut

    sent = []
    monkeypatch.setattr(live_mod, "send_alert", lambda m: sent.append(m))
    monkeypatch.setattr(live_mod, "_outbound_ip", lambda: "198.51.100.4")
    ex = FakeFut()

    def boom():
        raise Exception('binance {"code":-2015,"msg":"Invalid API-key, IP, or permissions"}')
    ex.sapi_get_account_apirestrictions = boom
    risk = RiskConfig(allow_short=True, max_leverage=3, allowed_symbols=["BTC/USDT"])
    for _ in range(3):
        with pytest.raises(SystemExit) as ei:
            LivePerpTrader(Bot(), stage="live", capital=1000.0, fee_bps=5, slippage_bps=0,
                           risk=risk, max_leverage=3, exchange=ex, live_dir=tmp_path)
        assert ei.value.code != HALT_EXIT_CODE   # auth failure stays restartable
    assert len(sent) == 1
    assert "198.51.100.4" not in sent[0]         # alert carries the masked IP only
