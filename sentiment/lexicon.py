"""Small, generic crypto-news sentiment lexicon for scoring headlines.

A lexicon is used deliberately instead of an LLM: reproducible, backtestable, free.
It is intentionally simple: every term weighs +1 (bullish) or -1 (bearish), and a
handful of unambiguous, high-impact terms weigh +2 / -2. Extend or replace it to
suit your sources; validate any change with scripts/validate_sentiment.py.

Scoring: matched weights are summed and squashed as  net / (sum(|w|) + 1),  which
stays inside (-1, 1), is 0 when nothing matches, and grows more confident as more
terms agree (one +1 term -> 0.5, two -> 0.67, one +1 and one -1 -> 0).
"""
from __future__ import annotations

import re

_BULLISH_TERMS = (
    "rally", "surge", "soar", "jump", "gain", "gains", "rebound", "recovery", "recover",
    "breakout", "bullish", "bull market", "record high", "new high", "all-time high",
    "all time high", "inflow", "inflows", "adoption", "partnership", "upgrade",
    "approval", "approved", "launch", "accumulation", "outperform", "rate cut",
    "buyback", "uptrend", "optimism",
)
_BEARISH_TERMS = (
    "crash", "plunge", "slump", "tumble", "drop", "fall", "falls", "decline", "sell-off",
    "selloff", "bearish", "bear market", "outflow", "outflows", "lawsuit", "sued",
    "sues", "ban", "banned", "crackdown", "investigation", "liquidation", "liquidated",
    "exploit", "stolen", "scam", "delist", "delisted", "rate hike", "recession",
    "downtrend", "panic", "fear", "warning",
)
_STRONG_BULLISH = ("etf approved", "etf approval")
_STRONG_BEARISH = ("hack", "hacked", "bankruptcy", "bankrupt", "insolvent", "fraud",
                   "rug pull", "collapse")

LEXICON: dict[str, float] = {
    **{t: 1.0 for t in _BULLISH_TERMS},
    **{t: -1.0 for t in _BEARISH_TERMS},
    **{t: 2.0 for t in _STRONG_BULLISH},
    **{t: -2.0 for t in _STRONG_BEARISH},
}

# Longer phrases are matched (and consumed) first, so "etf approval" is not also
# counted as "approval", and "sell-off" is matched as a whole.
_PATTERNS = [
    (re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])"), weight)
    for term, weight in sorted(LEXICON.items(), key=lambda kv: -len(kv[0]))
]


def score_text(text: str) -> float:
    """Sentiment of one headline in (-1, 1); 0.0 when no lexicon term matches."""
    t = text.lower()
    net, magnitude = 0.0, 0.0
    for pattern, weight in _PATTERNS:
        n = len(pattern.findall(t))
        if n:
            net += weight * n
            magnitude += abs(weight) * n
            t = pattern.sub(" ", t)
    if magnitude == 0:
        return 0.0
    return net / (magnitude + 1.0)
