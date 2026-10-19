"""Sentiment pipeline tests: lexicon, compositing, anti-lookahead, predictive-power check."""
import numpy as np
import pandas as pd
import pytest

from sentiment import composite as composite_mod
from sentiment import sources as sources_mod
from sentiment.composite import (
    AVAIL_DELAY_MS, DAY_MS, DEFAULT_SOURCES, SentimentConfig, as_of, build_composite,
    config_from_dict, regime,
)
from sentiment.lexicon import score_text
from sentiment.validate import ic_table, verdict

T0 = 1_600_000_000_000 - (1_600_000_000_000 % DAY_MS)  # aligned to a day boundary
ALL_SOURCES = SentimentConfig(sources=tuple(composite_mod.SOURCE_SPEC))


@pytest.fixture(autouse=True)
def default_config(monkeypatch):
    """Keep tests independent of whatever sentiment block system.yaml carries."""
    monkeypatch.setattr(composite_mod, "load_config", lambda: SentimentConfig())
    monkeypatch.setattr(sources_mod, "load_config", lambda: SentimentConfig())


@pytest.fixture()
def reddit_enabled(monkeypatch):
    monkeypatch.setattr(sources_mod, "load_config", lambda: ALL_SOURCES)


# ---- lexicon ----

def test_lexicon_bullish_and_bearish():
    assert score_text("Bitcoin hits all-time high after ETF approval") > 0.5
    assert score_text("Exchange hacked, funds stolen in $100M exploit") < -0.5
    assert score_text("Bitcoin price unchanged today") == 0.0


def test_lexicon_uniform_weights_and_normalization():
    assert score_text("prices rally") == pytest.approx(0.5)        # one +1 term
    assert score_text("rally and rebound") == pytest.approx(2 / 3)  # two agree
    assert score_text("rally then crash") == pytest.approx(0.0)    # cancel out
    assert score_text("exchange hacked") == pytest.approx(-2 / 3)  # strong term
    assert score_text("Spot ETF approved") == pytest.approx(2 / 3)  # strong phrase


def test_lexicon_phrase_not_double_counted():
    # "etf approval" is consumed as one strong phrase, not also "approval"
    assert score_text("etf approval") == pytest.approx(2 / 3)
    s = score_text("massive sell-off continues")
    assert s == pytest.approx(-0.5)


def test_lexicon_mixed_text_bounded():
    s = score_text("rally fades as crash fears mount, bullish traders liquidated")
    assert -1.0 < s < 1.0
    assert -1.0 < score_text(" ".join(["collapse"] * 50)) < 0


# ---- compositing ----

def _fg_df(values: list[float], start: int = T0) -> pd.DataFrame:
    return pd.DataFrame({
        "ts": [start + i * DAY_MS for i in range(len(values))],
        "value": values,
    })


def test_composite_needs_min_history():
    out = build_composite({"fear_greed": _fg_df([50] * 10)})
    assert out.empty  # fewer than 30 days -> no output


def test_composite_zscore_direction():
    # 60 days of fear (20) then 30 of greed (80) -> final score clearly positive
    out = build_composite({"fear_greed": _fg_df([20] * 60 + [80] * 30)})
    assert not out.empty
    assert out["score"].iloc[-1] > 30
    assert (out["score"].abs() <= 100).all()


def test_composite_avail_ts_has_delay():
    out = build_composite({"fear_greed": _fg_df([50] * 40)})
    assert ((out["avail_ts"] - out["ts"]) == AVAIL_DELAY_MS).all()


def test_as_of_no_lookahead():
    out = build_composite({"fear_greed": _fg_df(list(np.linspace(20, 80, 60)))})
    first = out.iloc[0]
    # not visible before avail_ts
    assert as_of(out, int(first["avail_ts"]) - 1) is None
    # visible exactly at avail_ts, with the right value
    assert as_of(out, int(first["avail_ts"])) == pytest.approx(first["score"])
    # at any time we get the latest value with avail_ts <= that time
    mid = out.iloc[30]
    got = as_of(out, int(mid["avail_ts"]) + 1)
    assert got == pytest.approx(mid["score"])


def test_as_of_empty():
    assert as_of(None, T0) is None
    assert as_of(pd.DataFrame(columns=["avail_ts", "score"]), T0) is None


def test_regime_labels():
    assert regime(-60) == "extreme_fear"
    assert regime(60) == "extreme_greed"
    assert regime(-55) == "neutral" and regime(55) == "neutral"
    assert regime(0) == "neutral"


def test_regime_custom_cutoffs():
    cfg = config_from_dict({"regime_cutoffs": [-30, 40]})
    assert regime(-30, cfg) == "extreme_fear"
    assert regime(35, cfg) == "neutral"
    assert regime(40, cfg) == "extreme_greed"


# ---- config ----

def test_config_defaults_are_neutral():
    cfg = config_from_dict(None)
    assert cfg.sources == DEFAULT_SOURCES
    assert "reddit" not in cfg.sources and "news" not in cfg.sources
    assert cfg.z_window == 60 and cfg.ema_span == 5
    assert (cfg.extreme_fear, cfg.extreme_greed) == (-60, 60)
    assert {cfg.weight(n) for n in cfg.sources} == {1.0}


def test_config_overrides_and_validation():
    cfg = config_from_dict({"sources": ["fear_greed", "reddit"], "weights": {"reddit": 0.5},
                            "z_window": 90, "ema_span": 3})
    assert cfg.sources == ("fear_greed", "reddit")
    assert cfg.weight("reddit") == 0.5 and cfg.weight("fear_greed") == 1.0
    assert cfg.z_window == 90 and cfg.ema_span == 3
    with pytest.raises(ValueError):
        config_from_dict({"sources": ["twitter"]})
    with pytest.raises(ValueError):
        config_from_dict({"weights": {"funding": -1}})
    with pytest.raises(ValueError):
        config_from_dict({"regime_cutoffs": [10, 60]})
    with pytest.raises(ValueError):
        config_from_dict({"z_window": 10})


def test_composite_multi_source_weighting():
    fg = _fg_df([20] * 60 + [80] * 30)
    funding = pd.DataFrame({
        "ts": [T0 + i * DAY_MS // 3 for i in range(270)],  # 8h frequency
        "value": [0.0001] * 180 + [0.0008] * 90,
    })
    out = build_composite({"fear_greed": fg, "funding": funding})
    assert not out.empty
    assert int(out["n_sources"].iloc[-1]) == 2
    assert "z_fear_greed" in out.columns and "z_funding" in out.columns
    assert (out["divergence"].dropna() >= 0).all()


def _two_sources():
    up = _fg_df(list(np.linspace(20, 80, 90)))
    down = _fg_df(list(np.linspace(80, 20, 90)))
    return up, down


def test_composite_equal_weights_cancel():
    # two mirror-image sources with equal weight -> blended z is ~0
    up, down = _two_sources()
    out = build_composite({"funding": up, "long_short": down})  # same shift, mirror values
    assert int(out["n_sources"].iloc[-1]) == 2
    assert abs(out["score"].iloc[-1]) < 1


def test_composite_ignores_disabled_sources():
    up, down = _two_sources()
    reddit = pd.concat([down.assign(ts=down.ts + k) for k in range(3)]).assign(weight=1.0)
    out = build_composite({"fear_greed": up, "reddit": reddit})  # reddit is off by default
    assert "z_reddit" not in out.columns
    assert int(out["n_sources"].max()) == 1
    on = build_composite({"fear_greed": up, "reddit": reddit}, ALL_SOURCES)
    assert "z_reddit" in on.columns


def test_composite_zero_weight_drops_source():
    up, down = _two_sources()
    cfg = config_from_dict({"weights": {"long_short": 0}})
    out = build_composite({"fear_greed": up, "long_short": down}, cfg)
    assert list(c for c in out.columns if c.startswith("z_")) == ["z_fear_greed"]


def test_composite_custom_window_and_span():
    fg = _fg_df(list(50 + 30 * np.sin(np.arange(150) / 4)))
    base = build_composite({"fear_greed": fg})
    slow = build_composite({"fear_greed": fg}, config_from_dict({"ema_span": 20}))
    # a longer EMA smooths more: the score swings less
    assert slow["score"].std() < base["score"].std()
    wide = build_composite({"fear_greed": fg}, config_from_dict({"z_window": 120}))
    assert not wide["score"].equals(base["score"])


# ---- predictive-power check ----

def test_ic_detects_planted_signal():
    # planted signal: higher sentiment -> higher next-day return, so IC(1d) must be clearly positive
    n = 300
    rng = np.random.default_rng(7)
    score = rng.uniform(-80, 80, n)
    closes = [100.0]
    for i in range(n - 1):
        closes.append(closes[-1] * (1 + score[i] / 8000 + rng.normal(0, 0.001)))
    sentiment = pd.DataFrame({
        "ts": [T0 + i * DAY_MS for i in range(n)],
        "avail_ts": [T0 + i * DAY_MS for i in range(n)],
        "score": score,
    })
    px = pd.DataFrame({"ts": [T0 + i * DAY_MS for i in range(n)], "close": closes})
    ic = ic_table(sentiment, px)
    assert ic[ic["horizon_days"] == 1]["ic"].iloc[0] > 0.3
    assert "momentum hypothesis" in verdict(ic)


def test_verdict_wording_is_neutral():
    ic = pd.DataFrame({"horizon_days": [1, 7], "ic": [-0.01, -0.2], "n": [100, 100]})
    v = verdict(ic)
    assert "contrarian hypothesis" in v and "buy signal" not in v


# ---- opt-in sources ----

def test_opt_in_sources_disabled_by_default(monkeypatch):
    def no_network(*_a, **_k):
        raise AssertionError("disabled source must not hit the network")
    monkeypatch.setattr(sources_mod, "_reddit_get", no_network)
    monkeypatch.setattr(sources_mod, "_get", no_network)
    assert sources_mod.fetch_reddit().empty
    assert sources_mod.fetch_news().empty


def test_fetch_enabled_only_calls_enabled(monkeypatch):
    called = []
    for name in ("fetch_fear_greed", "fetch_funding_rate", "fetch_long_short_ratio",
                 "fetch_news", "fetch_reddit"):
        monkeypatch.setattr(sources_mod, name,
                            lambda *a, _n=name, **k: called.append(_n) or pd.DataFrame())
    got = sources_mod.fetch_enabled()
    assert set(got) == set(DEFAULT_SOURCES)
    assert "fetch_news" not in called and "fetch_reddit" not in called


# ---- Reddit: RSS first, JSON fallback ----
_ATOM_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry><title>Daily Crypto Discussion - January 15</title><published>2024-01-15T00:00:05+00:00</published></entry>
 <entry><title>Bitcoin surges to new all-time high</title><published>2024-01-15T10:29:28+00:00</published></entry>
 <entry><title>Exchange hacked, funds stolen</title><published>2024-01-15T11:00:00+00:00</published></entry>
</feed>"""


def test_reddit_rss_skips_pinned_threads_equal_weight(monkeypatch, reddit_enabled):
    from sentiment import sources
    monkeypatch.setattr(sources, "_reddit_get", lambda url, retry_wait=15.0: _ATOM_SAMPLE)
    df = sources.fetch_reddit()
    assert len(df) == 2 and not df.title.str.startswith("Daily").any()
    assert (df.weight == 1.0).all()
    assert df.ts.is_monotonic_increasing and df.ts.iloc[0] == 1705314568000


def test_reddit_rss_failure_falls_back_to_json(monkeypatch, reddit_enabled):
    import json as _json
    from sentiment import sources

    def fake(url, retry_wait=15.0):
        if url.endswith(".rss"):
            raise OSError("HTTP Error 429")
        return _json.dumps({"data": {"children": [
            {"data": {"title": "ETH rally continues", "created_utc": 1705314568, "ups": 99, "stickied": False}},
            {"data": {"title": "pinned", "created_utc": 1705314500, "ups": 1, "stickied": True}}]}}).encode()
    monkeypatch.setattr(sources, "_reddit_get", fake)
    df = sources.fetch_reddit()
    assert len(df) == 1 and df.weight.iloc[0] > 1.0


def test_reddit_all_methods_fail_returns_empty(monkeypatch, reddit_enabled):
    from sentiment import sources

    def boom(url, retry_wait=15.0):
        raise OSError("HTTP Error 403: Blocked")
    monkeypatch.setattr(sources, "_reddit_get", boom)
    df = sources.fetch_reddit()
    assert df.empty and list(df.columns) == ["ts", "value", "weight", "title"]


# ---- hostile feeds ----
_EVIL_RSS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title><pubDate>Mon, 15 Jan 2024 10:00:00 GMT</pubDate></item></channel></rss>"""


def test_rss_with_entities_is_rejected(monkeypatch, reddit_enabled):
    monkeypatch.setattr(sources_mod, "_get", lambda url, timeout=15: _EVIL_RSS)
    monkeypatch.setattr(sources_mod, "_reddit_get", lambda url, retry_wait=15.0: _EVIL_RSS)
    assert sources_mod.fetch_news().empty
    with pytest.raises(Exception):
        sources_mod._reddit_rss("CryptoCurrency")


def test_response_size_is_capped():
    import io
    assert sources_mod._read_capped(io.BytesIO(b"x" * 10), limit=10) == b"x" * 10
    with pytest.raises(ValueError):
        sources_mod._read_capped(io.BytesIO(b"x" * 11), limit=10)


def test_reddit_warns_without_user_agent(monkeypatch, reddit_enabled, caplog):
    monkeypatch.setattr(sources_mod, "env", lambda k, d=None: None)
    monkeypatch.setattr(sources_mod, "_reddit_get", lambda url, retry_wait=15.0: _ATOM_SAMPLE)
    import logging
    sources_mod.log.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING):
            sources_mod.fetch_reddit()
    finally:
        sources_mod.log.removeHandler(caplog.handler)
    assert "TRIDENT_USER_AGENT" in caplog.text
