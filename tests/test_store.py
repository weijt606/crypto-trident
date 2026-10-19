"""TradeLog read-only mode."""
import sqlite3

import pytest

from core.models import Fill, Side
from data.store import TradeLog


def test_read_only_missing_file_is_empty_and_not_created(tmp_path):
    db = tmp_path / "trades.sqlite"
    t = TradeLog(db, read_only=True)
    assert t.fills_df().empty and list(t.fills_df().columns)[:2] == ["ts", "bot"]
    assert t.equity_df("demo-bot").empty
    assert t.events_df().empty
    assert t.latest_state("demo-bot") is None
    assert t.latest_strategy_state("demo-bot") is None
    t.close()
    assert not db.exists()


def test_read_only_reads_but_cannot_write(tmp_path):
    db = tmp_path / "trades.sqlite"
    w = TradeLog(db)
    w.record_equity(1, "demo-bot", 100.0, 100.0, 0.0, 10.0)
    w.close()
    r = TradeLog(db, read_only=True)
    assert len(r.equity_df("demo-bot")) == 1
    assert r.latest_state("demo-bot")["equity"] == 100.0
    with pytest.raises(PermissionError):
        r.record_event(2, "demo-bot", "x", "y")
    with pytest.raises(PermissionError):
        r.record_fill(Fill(ts=2, bot="demo-bot", symbol="BTC/USDT", side=Side.BUY,
                           qty=1.0, price=1.0, fee=0.0))
    with pytest.raises(sqlite3.OperationalError):   # the connection itself is mode=ro
        r.conn.execute("INSERT INTO events VALUES (3,'demo-bot','k','d')")
    r.close()


def test_read_only_tolerates_missing_tables(tmp_path):
    db = tmp_path / "old.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE equity (ts INTEGER, bot TEXT, equity REAL, cash REAL, "
              "position_qty REAL, last_price REAL)")
    c.commit(); c.close()
    r = TradeLog(db, read_only=True)
    assert r.fills_df().empty
    assert r.latest_strategy_state("demo-bot") is None
    r.close()
