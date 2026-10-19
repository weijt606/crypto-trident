"""Local storage: candles -> Parquet; fills/equity/events -> SQLite."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import storage_dir
from core.models import Fill

if TYPE_CHECKING:
    import pandas as pd

# pandas is imported lazily: paper/live executors only use TradeLog's sqlite
# reads/writes, and a top-level pandas import adds ~75MB to every long-running process.


def _bars_path(symbol: str, timeframe: str, exchange_id: str = "binance") -> Path:
    d = storage_dir() / "bars"
    safe_symbol = symbol.replace("/", "-")
    return d / f"{exchange_id}_{safe_symbol}_{timeframe}.parquet"


def save_bars(df: pd.DataFrame, symbol: str, timeframe: str, exchange_id: str = "binance") -> Path:
    """Incremental merge-save: de-duplicate against existing data by ts."""
    import pandas as pd
    path = _bars_path(symbol, timeframe, exchange_id)
    path.parent.mkdir(parents=True, exist_ok=True)   # only writers create the directory
    if path.exists():
        old = pd.read_parquet(path)
        df = (
            pd.concat([old, df])
            .drop_duplicates("ts", keep="last")
            .sort_values("ts")
            .reset_index(drop=True)
        )
    df.to_parquet(path, index=False)
    return path


def load_bars(symbol: str, timeframe: str, exchange_id: str = "binance") -> pd.DataFrame:
    import pandas as pd
    path = _bars_path(symbol, timeframe, exchange_id)
    if not path.exists():
        raise FileNotFoundError(
            f"no local candle data {path.name}; run first: uv run python scripts/fetch_data.py "
            f"--symbol {symbol} --timeframe {timeframe}"
        )
    return pd.read_parquet(path)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    ts INTEGER, bot TEXT, symbol TEXT, side TEXT,
    qty REAL, price REAL, fee REAL, realized_pnl REAL
);
CREATE TABLE IF NOT EXISTS equity (
    ts INTEGER, bot TEXT, equity REAL, cash REAL,
    position_qty REAL, last_price REAL
);
CREATE TABLE IF NOT EXISTS events (
    ts INTEGER, bot TEXT, kind TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS strategy_state (
    ts INTEGER, bot TEXT, state TEXT
);
CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills (ts);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity (ts);
"""


_COLUMNS = {
    "fills": ["ts", "bot", "symbol", "side", "qty", "price", "fee", "realized_pnl"],
    "equity": ["ts", "bot", "equity", "cash", "position_qty", "last_price"],
    "events": ["ts", "bot", "kind", "detail"],
    "strategy_state": ["ts", "bot", "state"],
}


class TradeLog:
    """Trade journal for paper/live trading, so everything can be audited later.

    read_only=True opens the database with an SQLite ``mode=ro`` URI: no schema
    is created, no pragma is set and nothing can be written. A missing file is
    not created; every read then returns an empty result. Missing tables (an
    older ledger) also read as empty. Readers such as the dashboard use this so
    they can never create or modify a ledger.
    """

    def __init__(self, db_path: Path | str | None = None, read_only: bool = False):
        self.db_path = Path(db_path) if db_path else storage_dir() / "trades.sqlite"
        self.read_only = read_only
        self.conn: sqlite3.Connection | None
        if read_only:
            self._tables: set[str] = set()
            if not self.db_path.is_file():
                self.conn = None
                return
            uri = self.db_path.resolve().as_uri() + "?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True, timeout=30)
            self._tables = {r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            return
        # timeout: several paper processes share one DB; wait on write-lock contention instead of erroring
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.execute("PRAGMA busy_timeout=30000")
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # another process may be converting to WAL on simultaneous first start; the connection is still usable
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self._tables = set(_COLUMNS)

    def _readable(self, table: str) -> bool:
        return self.conn is not None and table in self._tables

    def _write_conn(self) -> sqlite3.Connection:
        if self.read_only or self.conn is None:
            raise PermissionError(f"TradeLog {self.db_path.name} is opened read-only")
        return self.conn

    def _read_df(self, table: str, bot: str | None) -> pd.DataFrame:
        import pandas as pd
        if not self._readable(table):
            return pd.DataFrame(columns=_COLUMNS[table])
        q, args = f"SELECT * FROM {table}", ()
        if bot:
            q, args = q + " WHERE bot=?", (bot,)
        return pd.read_sql(q + " ORDER BY ts", self.conn, params=args)

    def record_fill(self, fill: Fill) -> None:
        conn = self._write_conn()
        conn.execute(
            "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?)",
            (fill.ts, fill.bot, fill.symbol, fill.side.value,
             fill.qty, fill.price, fill.fee, fill.realized_pnl),
        )
        conn.commit()

    def record_equity(self, ts: int, bot: str, equity: float, cash: float,
                      position_qty: float, last_price: float) -> None:
        conn = self._write_conn()
        conn.execute(
            "INSERT INTO equity VALUES (?,?,?,?,?,?)",
            (ts, bot, equity, cash, position_qty, last_price),
        )
        conn.commit()

    def record_event(self, ts: int, bot: str, kind: str, detail: str) -> None:
        conn = self._write_conn()
        conn.execute("INSERT INTO events VALUES (?,?,?,?)", (ts, bot, kind, detail))
        conn.commit()

    def record_strategy_state(self, ts: int, bot: str, state_json: str) -> None:
        """Overwrite the strategy state snapshot each bar (latest only; history lives in fills/equity)."""
        conn = self._write_conn()
        conn.execute("DELETE FROM strategy_state WHERE bot=?", (bot,))
        conn.execute("INSERT INTO strategy_state VALUES (?,?,?)", (ts, bot, state_json))
        conn.commit()

    def latest_strategy_state(self, bot: str) -> dict | None:
        if not self._readable("strategy_state"):
            return None
        row = self.conn.execute(
            "SELECT ts, state FROM strategy_state WHERE bot=? ORDER BY ts DESC LIMIT 1",
            (bot,),
        ).fetchone()
        if row is None:
            return None
        import json
        return {"ts": row[0], **json.loads(row[1])}

    def fills_df(self, bot: str | None = None) -> pd.DataFrame:
        return self._read_df("fills", bot)

    def equity_df(self, bot: str | None = None) -> pd.DataFrame:
        return self._read_df("equity", bot)

    def events_df(self, bot: str | None = None) -> pd.DataFrame:
        return self._read_df("events", bot)

    def latest_state(self, bot: str) -> dict | None:
        """Resume after restart: restore the most recent account snapshot."""
        if not self._readable("equity"):
            return None
        row = self.conn.execute(
            "SELECT ts, cash, position_qty, equity, last_price FROM equity "
            "WHERE bot=? ORDER BY ts DESC LIMIT 1",
            (bot,),
        ).fetchone()
        if row is None:
            return None
        return {"ts": row[0], "cash": row[1], "position_qty": row[2],
                "equity": row[3], "last_price": row[4]}

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
