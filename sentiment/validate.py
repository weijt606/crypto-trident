"""Predictive-power check for sentiment indicators: a hard gate before trading on them.

IC (information coefficient) = Spearman(sentiment_t, forward h-day return).
Quantile table = mean forward return per sentiment quintile (checks monotonicity).

Two competing hypotheses are tested, neither is assumed: momentum (sentiment and
forward returns move together, IC > 0) and contrarian (they move oppositely, IC < 0).
"""
from __future__ import annotations

import pandas as pd

DAY_MS = 86_400_000

HORIZONS = (1, 3, 7, 30)
IC_THRESHOLD = 0.03  # |IC| below this is treated as no predictive power


def _merge_asof(sentiment: pd.DataFrame, daily_close: pd.DataFrame) -> pd.DataFrame:
    """daily_close: DataFrame[ts, close] (daily bars). Aligned on avail_ts to avoid lookahead."""
    px = daily_close.sort_values("ts").reset_index(drop=True)
    merged = pd.merge_asof(
        px, sentiment[["avail_ts", "score"]].sort_values("avail_ts"),
        left_on="ts", right_on="avail_ts", direction="backward",
    )
    return merged.dropna(subset=["score"]).reset_index(drop=True)


def ic_table(sentiment: pd.DataFrame, daily_close: pd.DataFrame) -> pd.DataFrame:
    """IC and sample count per horizon."""
    m = _merge_asof(sentiment, daily_close)
    rows = []
    for h in HORIZONS:
        fwd = m["close"].shift(-h) / m["close"] - 1
        valid = pd.DataFrame({"score": m["score"], "fwd": fwd}).dropna()
        ic = valid["score"].corr(valid["fwd"], method="spearman") if len(valid) > 30 else float("nan")
        rows.append({"horizon_days": h, "ic": round(float(ic), 4), "n": len(valid)})
    return pd.DataFrame(rows)


def quantile_table(sentiment: pd.DataFrame, daily_close: pd.DataFrame,
                   horizon: int = 7, q: int = 5) -> pd.DataFrame:
    """Sentiment q-quantiles x mean forward return over `horizon` days."""
    m = _merge_asof(sentiment, daily_close)
    m["fwd"] = m["close"].shift(-horizon) / m["close"] - 1
    m = m.dropna(subset=["fwd"])
    if len(m) < q * 10:
        return pd.DataFrame()
    m["bucket"] = pd.qcut(m["score"], q, labels=False, duplicates="drop")
    out = m.groupby("bucket").agg(
        score_mean=("score", "mean"), fwd_ret_mean=("fwd", "mean"),
        fwd_ret_median=("fwd", "median"), n=("fwd", "count"),
    ).reset_index()
    out["bucket"] = out["bucket"].map(lambda b: f"Q{int(b) + 1}")
    return out.round(4)


def verdict(ic_df: pd.DataFrame) -> str:
    """One-line conclusion: is there predictive power, and in which direction."""
    best = ic_df.loc[ic_df["ic"].abs().idxmax()]
    if abs(best["ic"]) < IC_THRESHOLD:
        return (f"No significant predictive power (best |IC|={abs(best['ic']):.3f} < {IC_THRESHOLD}); "
                "monitor only, do not trade on it")
    direction = ("consistent with the momentum hypothesis (IC > 0)" if best["ic"] > 0
                 else "consistent with the contrarian hypothesis (IC < 0)")
    return (
        f"horizon={int(best['horizon_days'])}d IC={best['ic']:.3f}, direction: {direction}. "
        "Not a trading signal by itself: confirm quantile monotonicity out-of-sample first"
    )
