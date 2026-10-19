"""Denoising and compositing: multiple sentiment sources -> one score in [-100, 100].

Pipeline:
daily alignment -> anti-lookahead shift -> rolling z-score (clipped at +/-3)
-> weighted blend -> EMA -> tanh squash

Everything tunable lives in an optional ``sentiment:`` block in config/system.yaml
(see config_from_dict). Defaults are deliberately neutral: equal weights across the
enabled sources, a 60-day z-score window, EMA span 5 and regime cutoffs at +/-60.

Anti-lookahead contract: every output row carries avail_ts, the earliest moment a
strategy may see that value. Executors only inject values with avail_ts <= bar.ts.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DAY_MS = 86_400_000

# Per-source anti-lookahead shift (in days) and optional minimum items per day.
# Weights are not set here: see SentimentConfig.weights.
SOURCE_SPEC: dict[str, dict] = {
    "fear_greed":  {"shift": 0},                  # published at the start of the day
    "funding":     {"shift": 1},                  # intraday aggregate -> available next day
    "long_short":  {"shift": 1},
    "news":        {"shift": 1, "min_items": 3},  # opt-in (RSS)
    "reddit":      {"shift": 1, "min_items": 3},  # opt-in (Reddit Data API terms apply)
}

# Public-API sources enabled when config does not say otherwise.
DEFAULT_SOURCES: tuple[str, ...] = ("fear_greed", "funding", "long_short")

Z_WINDOW = 60       # rolling z-score window (days)
Z_MIN_PERIODS = 30
Z_CLIP = 3.0
EMA_SPAN = 5
AVAIL_DELAY_MS = 3_600_000  # available 1h after the day boundary (buffer for publish/fetch delay)

EXTREME_FEAR = -60.0
EXTREME_GREED = 60.0


@dataclass(frozen=True)
class SentimentConfig:
    sources: tuple[str, ...] = DEFAULT_SOURCES
    weights: dict[str, float] = field(default_factory=dict)  # empty -> equal weights
    z_window: int = Z_WINDOW
    ema_span: int = EMA_SPAN
    extreme_fear: float = EXTREME_FEAR
    extreme_greed: float = EXTREME_GREED

    def weight(self, name: str) -> float:
        return float(self.weights.get(name, 1.0))


def config_from_dict(raw: dict | None) -> SentimentConfig:
    """Build a SentimentConfig from a ``sentiment:`` mapping (missing keys -> defaults).

    Example (all keys optional)::

        sentiment:
          sources: [fear_greed, funding, long_short]   # add news / reddit to opt in
          weights: {fear_greed: 1, funding: 1}          # omitted sources weigh 1
          z_window: 60
          ema_span: 5
          regime_cutoffs: [-60, 60]
    """
    raw = raw or {}
    srcs = raw.get("sources")
    sources = tuple(srcs) if srcs is not None else DEFAULT_SOURCES
    unknown = set(sources) - set(SOURCE_SPEC)
    if unknown:
        raise ValueError(f"unknown sentiment sources {sorted(unknown)}; known: {sorted(SOURCE_SPEC)}")
    weights = {str(k): float(v) for k, v in (raw.get("weights") or {}).items()}
    if any(w < 0 for w in weights.values()):
        raise ValueError("sentiment weights must be >= 0")
    lo, hi = raw.get("regime_cutoffs", (EXTREME_FEAR, EXTREME_GREED))
    if not lo < 0 < hi:
        raise ValueError("sentiment regime_cutoffs must be [negative, positive]")
    z_window = int(raw.get("z_window", Z_WINDOW))
    if z_window < Z_MIN_PERIODS:
        raise ValueError(f"sentiment z_window must be >= {Z_MIN_PERIODS}")
    return SentimentConfig(
        sources=sources, weights=weights, z_window=z_window,
        ema_span=int(raw.get("ema_span", EMA_SPAN)),
        extreme_fear=float(lo), extreme_greed=float(hi),
    )


def load_config() -> SentimentConfig:
    """Read the optional ``sentiment:`` block from config/system.yaml."""
    try:
        from core.config import load_system_config
        raw = (load_system_config() or {}).get("sentiment")
    except FileNotFoundError:
        raw = None
    return config_from_dict(raw)


def _to_daily(df: pd.DataFrame, spec: dict) -> pd.Series:
    """Raw series -> UTC daily mean (with optional min-item threshold and weighting)."""
    d = df.copy()
    d["day"] = (d["ts"] // DAY_MS) * DAY_MS
    if "weight" in d.columns:  # reddit: popularity-weighted mean
        grouped = d.groupby("day").apply(
            lambda g: np.average(g["value"], weights=g["weight"] + 1e-9),
            include_groups=False,
        )
    else:
        grouped = d.groupby("day")["value"].mean()
    min_items = spec.get("min_items")
    if min_items:
        counts = d.groupby("day")["value"].count()
        grouped = grouped.where(counts >= min_items)
    return grouped


def _rolling_z(s: pd.Series, window: int) -> pd.Series:
    mean = s.rolling(window, min_periods=Z_MIN_PERIODS).mean()
    std = s.rolling(window, min_periods=Z_MIN_PERIODS).std()
    return ((s - mean) / std.replace(0, np.nan)).clip(-Z_CLIP, Z_CLIP)


def build_composite(sources: dict[str, pd.DataFrame],
                    cfg: SentimentConfig | None = None) -> pd.DataFrame:
    """sources: {source name: raw df}. Sources not enabled in cfg are ignored.
    Returns a daily composite table: ts, avail_ts, score, z, n_sources, divergence, z_{source}...
    """
    cfg = cfg or load_config()
    z_cols: dict[str, pd.Series] = {}
    for name, df in sources.items():
        if name not in cfg.sources or cfg.weight(name) <= 0 or df is None or df.empty:
            continue
        spec = SOURCE_SPEC[name]
        daily = _to_daily(df, spec)
        daily = daily.shift(spec["shift"])  # anti-lookahead
        z = _rolling_z(daily, cfg.z_window)
        if z.notna().any():
            z_cols[name] = z

    if not z_cols:
        return pd.DataFrame(
            columns=["ts", "avail_ts", "score", "z", "n_sources", "divergence"]
        )

    table = pd.DataFrame(z_cols).sort_index()
    weights = pd.Series({n: cfg.weight(n) for n in table.columns})

    available = table.notna()
    w_matrix = available.mul(weights, axis=1)
    z_weighted = (table.fillna(0) * w_matrix).sum(axis=1) / w_matrix.sum(axis=1)

    z_smooth = z_weighted.ewm(span=cfg.ema_span).mean()
    score = np.tanh(z_smooth / 1.5) * 100

    out = pd.DataFrame({
        "ts": table.index.astype("int64"),
        "avail_ts": table.index.astype("int64") + AVAIL_DELAY_MS,
        "score": score.values,
        "z": z_smooth.values,
        "n_sources": available.sum(axis=1).values,
        "divergence": (table.max(axis=1) - table.min(axis=1)).values,
    })
    for name in table.columns:
        out[f"z_{name}"] = table[name].values
    return out.dropna(subset=["score"]).reset_index(drop=True)


def regime(score: float, cfg: SentimentConfig | None = None) -> str:
    cfg = cfg or load_config()
    if score <= cfg.extreme_fear:
        return "extreme_fear"
    if score >= cfg.extreme_greed:
        return "extreme_greed"
    return "neutral"


def as_of(composite: pd.DataFrame, ts: int) -> float | None:
    """Latest score available at ts (avail_ts <= ts), or None."""
    if composite is None or composite.empty:
        return None
    eligible = composite[composite["avail_ts"] <= ts]
    if eligible.empty:
        return None
    return float(eligible["score"].iloc[-1])
