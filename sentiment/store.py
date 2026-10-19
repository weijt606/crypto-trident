"""Sentiment storage: storage/sentiment/{source}.parquet, merged incrementally."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.config import storage_dir


def _dir() -> Path:
    d = storage_dir() / "sentiment"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_source(name: str, df: pd.DataFrame) -> Path | None:
    """Incremental save (dedupe on ts, newer values win). Empty frames are skipped."""
    if df is None or df.empty:
        return None
    path = _dir() / f"{name}.parquet"
    if path.exists():
        old = pd.read_parquet(path)
        df = pd.concat([old, df]).drop_duplicates("ts", keep="last")
    df = df.sort_values("ts").reset_index(drop=True)
    df.to_parquet(path, index=False)
    return path


def load_source(name: str) -> pd.DataFrame | None:
    path = _dir() / f"{name}.parquet"
    return pd.read_parquet(path) if path.exists() else None


def save_composite(df: pd.DataFrame) -> Path:
    path = _dir() / "composite.parquet"
    df.to_parquet(path, index=False)
    return path


def load_composite() -> pd.DataFrame | None:
    path = _dir() / "composite.parquet"
    return pd.read_parquet(path) if path.exists() else None
