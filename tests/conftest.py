import os
import sys
import tempfile
from pathlib import Path

# Tests must never write into the repository. Point storage and logs at a temp dir
# before any project module is imported (loggers open their file at import time).
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="trident-tests-"))
os.environ["TRIDENT_STORAGE_DIR"] = str(_TMP_ROOT / "storage")
os.environ["TRIDENT_LOG_DIR"] = str(_TMP_ROOT / "logs")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest

from core.models import AccountView, Bar

HOUR_MS = 3_600_000
T0 = 1_699_920_000_000  # 2023-11-14 00:00 UTC, start of a UTC day


def make_bars(closes: list[float], start_ts: int = T0) -> pd.DataFrame:
    """Build synthetic candles from a close series (open = previous close)."""
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        hi, lo = max(prev, c) * 1.001, min(prev, c) * 0.999
        rows.append({"ts": start_ts + i * HOUR_MS, "open": prev, "high": hi,
                     "low": lo, "close": c, "volume": 100.0})
        prev = c
    return pd.DataFrame(rows)


def make_bar(ts: int, close: float, symbol: str = "BTC/USDT") -> Bar:
    return Bar(ts=ts, open=close, high=close, low=close, close=close,
               volume=1.0, symbol=symbol, timeframe="1h")


def make_account(cash: float = 1000.0, qty: float = 0.0, price: float = 100.0,
                 avg_cost: float = 0.0) -> AccountView:
    return AccountView(cash=cash, position_qty=qty, avg_cost=avg_cost, last_price=price)


@pytest.fixture
def t0() -> int:
    return T0


@pytest.fixture(autouse=True)
def _no_alert_pollution(monkeypatch):
    """Global: send_alert in tests must never write to the real
    storage/alerts.jsonl (fake alerts would show up on the dashboard).
    Persistence is stubbed out."""
    monkeypatch.setattr("monitor.alerts._persist", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Each test gets its own storage dir, so ledgers/heartbeats never leak between tests."""
    monkeypatch.setenv("TRIDENT_STORAGE_DIR", str(tmp_path / "storage"))
