"""Performance metrics tests."""
import pandas as pd
import pytest

from backtest.metrics import compute_metrics, max_drawdown, sharpe
from conftest import HOUR_MS, T0


def test_max_drawdown_known_value():
    eq = pd.Series([100, 120, 90, 110, 80])
    # peak 120 -> trough 80 = -33.3%
    assert max_drawdown(eq) == pytest.approx(1 - 80 / 120)


def test_max_drawdown_monotonic_up_is_zero():
    assert max_drawdown(pd.Series([100, 110, 120])) == 0.0


def test_sharpe_zero_variance():
    assert sharpe(pd.Series([100.0, 100.0, 100.0]), "1h") == 0.0


def test_compute_metrics_basics():
    n = 24
    curve = pd.DataFrame({
        "ts": [T0 + i * HOUR_MS for i in range(n)],
        "equity": [1000 + i for i in range(n)],
        "close": [100 + i for i in range(n)],
    })
    fills = pd.DataFrame({
        "side": ["buy", "sell"],
        "fee": [0.3, 0.3],
        "realized_pnl": [0.0, 5.0],
    })
    m = compute_metrics(curve, fills, "1h", 1000)
    assert m["final_equity"] == 1023
    assert m["total_return"] == pytest.approx(23 / 1000, abs=1e-4)
    assert m["cagr"] is None  # span < 30 days: not annualized
    assert m["n_round_trips"] == 1
    assert m["win_rate"] == 1.0
    assert m["total_fees"] == pytest.approx(0.6)
    assert m["buy_hold_return"] == pytest.approx(23 / 100, abs=1e-4)


def test_corrupted_cost_basis_fill_detected_and_reported():
    """A lost avg_cost books (almost) the full sale proceeds as profit:
    realized >= ~95% of proceeds means implied cost ~0 -> excluded from the
    clean figures and reported separately; win rate is cleaned too."""
    import pandas as pd
    from backtest.metrics import compute_metrics
    curve = pd.DataFrame({"ts": [0, 3_600_000], "equity": [100.0, 101.0],
                          "close": [50.0, 51.0]})
    fills = pd.DataFrame([
        {"side": "buy",  "qty": 0.4, "price": 50.0, "fee": 0.01, "realized_pnl": 0.0},
        {"side": "sell", "qty": 0.4, "price": 51.0, "fee": 0.01, "realized_pnl": 0.39},   # normal fill (win)
        {"side": "sell", "qty": 0.2, "price": 50.0, "fee": 0.01, "realized_pnl": 10.0},   # corrupted: ~full proceeds
        {"side": "sell", "qty": 0.1, "price": 50.0, "fee": 0.01, "realized_pnl": -0.2},   # normal fill (loss)
    ])
    m = compute_metrics(curve, fills, "1h", 100.0)
    assert m["n_tainted_fills"] == 1
    assert m["realized_pnl_tainted"] == pytest.approx(10.0)
    assert m["realized_pnl"] == pytest.approx(10.19)          # raw ledger figure kept
    assert m["realized_pnl_clean"] == pytest.approx(0.19)     # after exclusion
    assert m["win_rate"] == pytest.approx(0.5)                # 1 win, 1 loss; corrupted fill not counted


def test_no_corrupted_fills_no_extra_fields():
    import pandas as pd
    from backtest.metrics import compute_metrics
    curve = pd.DataFrame({"ts": [0, 3_600_000], "equity": [100.0, 101.0],
                          "close": [50.0, 51.0]})
    fills = pd.DataFrame([
        {"side": "buy",  "qty": 0.4, "price": 50.0, "fee": 0.01, "realized_pnl": 0.0},
        {"side": "sell", "qty": 0.4, "price": 51.0, "fee": 0.01, "realized_pnl": 0.39},
    ])
    m = compute_metrics(curve, fills, "1h", 100.0)
    assert "realized_pnl_clean" not in m and "n_tainted_fills" not in m
    assert m["win_rate"] == pytest.approx(1.0)


def test_cagr_reported_for_long_spans():
    day_ms = 86_400_000
    curve = pd.DataFrame({"ts": [T0, T0 + 365 * day_ms], "equity": [1000.0, 1100.0],
                          "close": [100.0, 110.0]})
    m = compute_metrics(curve, pd.DataFrame(), "1d", 1000)
    assert m["cagr"] == pytest.approx(0.1, abs=1e-3)
