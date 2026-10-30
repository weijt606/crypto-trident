"""Sentiment source fetchers. Public endpoints only (no API keys), all fail-soft:
on failure they log a warning and return an empty frame.

Every fetcher returns a DataFrame with at least: ts (epoch ms), value (float).

Enabled sources come from the optional ``sentiment.sources`` list in
config/system.yaml (default: fear_greed, funding, long_short). News RSS and Reddit
are opt-in: their fetchers return an empty frame unless listed there. Each source
has its own terms of use; see "Data sources and terms" in README.md.
"""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request

import pandas as pd
# defusedxml rejects DTDs/entity expansion (billion laughs, external entities).
from defusedxml import ElementTree as ET

from core.config import env
from data.fetcher import make_exchange
from monitor.logger import get_logger
from sentiment.composite import SentimentConfig, load_config
from sentiment.lexicon import score_text

log = get_logger("sentiment")

# Set TRIDENT_USER_AGENT to a descriptive value with contact info, e.g.
# "trident/0.1 (by u/yourname; you@example.com)". Reddit requires this.
_UA = {"User-Agent": env("TRIDENT_USER_AGENT", "trident/0.1") or "trident/0.1"}

# Upper bound on any single HTTP response body; feeds and JSON endpoints used
# here are far smaller, so anything bigger is treated as an error.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

EMPTY = pd.DataFrame(columns=["ts", "value"])


def _enabled(name: str, cfg: SentimentConfig | None) -> bool:
    if name in (cfg or load_config()).sources:
        return True
    log.info("%s source disabled (add it to sentiment.sources in system.yaml to opt in)", name)
    return False


def _read_capped(resp, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"response larger than {limit} bytes")
    return data


def _get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _read_capped(resp)


def fetch_fear_greed(limit: int = 0) -> pd.DataFrame:
    """Alternative.me Fear & Greed index. limit=0 fetches the full history.
    alternative.me asks that the source be credited wherever the value is shown."""
    try:
        raw = json.loads(_get(f"https://api.alternative.me/fng/?limit={limit}"))
        rows = [
            {"ts": int(d["timestamp"]) * 1000, "value": float(d["value"])}
            for d in raw["data"]
        ]
        return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    except Exception as e:
        log.warning("Fear & Greed fetch failed: %s", e)
        return EMPTY.copy()


def fetch_funding_rate(symbol: str = "BTC/USDT:USDT", days: int = 730) -> pd.DataFrame:
    """Binance perpetual funding-rate history (every 8h, years of backfill available)."""
    try:
        ex = make_exchange("binanceusdm")
        since = ex.milliseconds() - days * 86_400_000
        rows: list[dict] = []
        while True:
            batch = ex.fetch_funding_rate_history(symbol, since=since, limit=1000)
            if not batch:
                break
            rows.extend(
                {"ts": b["timestamp"], "value": float(b["fundingRate"])} for b in batch
            )
            since = batch[-1]["timestamp"] + 1
            if len(batch) < 1000:
                break
        return pd.DataFrame(rows).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    except Exception as e:
        log.warning("funding rate fetch failed: %s", e)
        return EMPTY.copy()


def fetch_long_short_ratio(symbol: str = "BTCUSDT", period: str = "1h") -> pd.DataFrame:
    """Binance global account long/short ratio (exchange keeps only the last 30 days)."""
    try:
        url = (
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
            f"?symbol={symbol}&period={period}&limit=500"
        )
        raw = json.loads(_get(url))
        rows = [
            {"ts": int(d["timestamp"]), "value": float(d["longShortRatio"])}
            for d in raw
        ]
        return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    except Exception as e:
        log.warning("long/short ratio fetch failed: %s", e)
        return EMPTY.copy()


_RSS_FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
}


def fetch_news(cfg: SentimentConfig | None = None) -> pd.DataFrame:
    """News RSS headlines -> lexicon score. Columns: ts, value, title, src.
    Opt-in: returns an empty frame unless "news" is in sentiment.sources."""
    rows: list[dict] = []
    if not _enabled("news", cfg):
        return pd.DataFrame(columns=["ts", "value", "title", "src"])
    for src, url in _RSS_FEEDS.items():
        try:
            root = ET.fromstring(_get(url))
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                pub = item.findtext("pubDate")
                if not title or not pub:
                    continue
                ts = int(pd.Timestamp(pub).timestamp() * 1000)
                rows.append(
                    {"ts": ts, "value": score_text(title), "title": title, "src": src}
                )
        except Exception as e:
            log.warning("RSS %s fetch failed: %s", src, e)
    if not rows:
        return pd.DataFrame(columns=["ts", "value", "title", "src"])
    return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)


# Reddit: try the public Atom feed first, with JSON fallback for transport or
# parsing failures only. Access denials and rate limits stop this fetch; switching
# formats must not bypass those responses. RSS posts are equally weighted.
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_REDDIT_COLS = ["ts", "value", "weight", "title"]
# RSS does not flag pinned posts; exclude recurring discussion threads by title so
# both paths match (the JSON path skips them via `stickied`).
_PINNED = re.compile(r"^(Daily|Weekly|Monthly) .*(Discussion|Thread)", re.I)


def _reddit_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return _read_capped(resp)


def _reddit_rss(subreddit: str) -> list[dict]:
    from datetime import datetime
    root = ET.fromstring(_reddit_get(f"https://www.reddit.com/r/{subreddit}/hot/.rss"))
    rows = []
    for e in root.findall("a:entry", _ATOM):
        title = (e.findtext("a:title", "", _ATOM) or "").strip()
        when = e.findtext("a:published", None, _ATOM) or e.findtext("a:updated", None, _ATOM)
        if not title or not when or _PINNED.match(title):
            continue
        rows.append({"ts": int(datetime.fromisoformat(when).timestamp() * 1000),
                     "value": score_text(title), "weight": 1.0, "title": title})
    return rows


def _reddit_json(subreddit: str, limit: int) -> list[dict]:
    raw = json.loads(_reddit_get(f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"))
    rows = []
    for child in raw["data"]["children"]:
        d = child["data"]
        if d.get("stickied"):
            continue
        title = d.get("title", "")
        rows.append({"ts": int(d["created_utc"] * 1000), "value": score_text(title),
                     "weight": math.log1p(max(0, d.get("ups", 0))), "title": title})
    return rows


def fetch_reddit(subreddit: str = "CryptoCurrency", limit: int = 75,
                 cfg: SentimentConfig | None = None) -> pd.DataFrame:
    """Reddit hot-post titles -> lexicon score. Tries RSS first, then JSON; returns an
    empty frame (with a warning) if both fail or access is denied/rate-limited.
    Columns: ts, value, weight (RSS: 1.0; JSON: log(1+upvotes)), title.
    Opt-in: returns an empty frame unless "reddit" is in sentiment.sources."""
    if not _enabled("reddit", cfg):
        return pd.DataFrame(columns=_REDDIT_COLS)
    if env("TRIDENT_REDDIT_ACCESS_APPROVED") != "true":
        log.warning("Reddit fetch skipped: obtain the required access approval first, then set "
                    "TRIDENT_REDDIT_ACCESS_APPROVED=true. This setting is an operator "
                    "acknowledgement, not proof of permission.")
        return pd.DataFrame(columns=_REDDIT_COLS)
    if not env("TRIDENT_USER_AGENT"):
        log.warning("Reddit source enabled but TRIDENT_USER_AGENT is unset; Reddit requires a "
                    "descriptive User-Agent with contact info, e.g. "
                    "\"trident/0.1 (by u/yourname; you@example.com)\"")
        return pd.DataFrame(columns=_REDDIT_COLS)
    errors = []
    for name, fn in (("RSS", lambda: _reddit_rss(subreddit)), ("JSON", lambda: _reddit_json(subreddit, limit))):
        try:
            rows = fn()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429, 451):
                log.warning("Reddit fetch stopped after HTTP %s; no retry or format fallback. "
                            "Review access approval and rate limits before another run.", e.code)
                return pd.DataFrame(columns=_REDDIT_COLS)
            errors.append(f"{name}: HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001  fail-soft, try the next method
            errors.append(f"{name}: {e}")
            continue
        if rows:
            log.info("Reddit: %s returned %d posts", name, len(rows))
            return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
        errors.append(f"{name}: 0 posts")
    log.warning("Reddit fetch failed (all methods): %s", " | ".join(errors))
    return pd.DataFrame(columns=_REDDIT_COLS)


def fetch_enabled(backfill: bool = False, cfg: SentimentConfig | None = None) -> dict[str, pd.DataFrame]:
    """Fetch every source enabled in config. backfill=True pulls all available history."""
    cfg = cfg or load_config()
    fetchers = {
        "fear_greed": lambda: fetch_fear_greed(limit=0 if backfill else 7),
        "funding": lambda: fetch_funding_rate(days=2900 if backfill else 7),
        "long_short": fetch_long_short_ratio,
        "news": lambda: fetch_news(cfg),
        "reddit": lambda: fetch_reddit(cfg=cfg),
    }
    return {name: fetchers[name]() for name in cfg.sources if name in fetchers}
