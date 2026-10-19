"""Local monitoring dashboard (stdlib HTTP server, no extra dependencies).

All data is read from local storage: the SQLite trade logs (paper and live),
Parquet bar files and, if present, the sentiment composite table. Only the
market ticker cards and the live tail of the kline chart hit the exchange,
and only through public (unauthenticated) endpoints. The dashboard never
loads trade keys.

Binds to 127.0.0.1 only; it is not meant to be exposed to a network. Requests
whose Host header is not 127.0.0.1:<port> or localhost:<port> are rejected
(DNS-rebinding defense), and every response carries a strict CSP. The page
loads no third-party resources: ECharts is vendored under monitor/static/.

Run: uv run python -m monitor.dashboard [--port 8777]  ->  http://127.0.0.1:8777
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import yaml

from core.config import CONFIG_DIR, load_system_config, storage_dir
from core.polling import base_interval
from data.store import TradeLog, load_bars
from monitor.heartbeat import last_beat
from monitor.logger import get_logger
from strategies import STRATEGIES

log = get_logger("dashboard")

STATIC_DIR = Path(__file__).parent / "static"
# Fixed allow-list of static assets: URL path -> (file name, content type).
# Nothing outside this table is ever read from disk, so no path traversal.
STATIC_FILES = {
    "/static/echarts.min.js": ("echarts.min.js", "application/javascript; charset=utf-8"),
    "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/ECHARTS_LICENSE": ("ECHARTS_LICENSE", "text/plain; charset=utf-8"),
}
# script-src has no 'unsafe-inline': all page code lives in static/app.js and
# there are no inline event handlers. style-src keeps 'unsafe-inline' because
# ECharts tooltips and a few rendered rows carry computed style="" attributes.
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; "
                                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                "connect-src 'self'; object-src 'none'; base-uri 'none'; "
                                "form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
DEFAULT_UNIVERSE = ["BTC/USDT", "ETH/USDT"]
ALLOWED_TF = ("5m", "15m", "1h", "4h", "1d")
# Kline `limit` is snapped up to one of these buckets so the kline cache key
# space stays bounded no matter what the client sends.
KLINE_LIMITS = (100, 250, 500, 1000, 2000)
ALERTS_TAIL_BYTES = 256 * 1024      # /api/alerts reads at most this much of alerts.jsonl
REQUEST_TIMEOUT_S = 15              # per-connection socket timeout

# A single shared exchange instance: ccxt's client-side rate limiter is per
# instance, so creating one per request would defeat throttling. Only raw
# public endpoints are used, which do not trigger load_markets().
_exchange = None


def _ex():
    global _exchange
    if _exchange is None:
        import ccxt
        _exchange = ccxt.binance({"enableRateLimit": True, "timeout": 20_000})
    return _exchange


def _spot_id(symbol: str) -> str:
    return symbol.replace("/", "")


# ---------------------------------------------------------------- config ---

_cfg_cache: dict = {"hit": None}   # (signature, data) tuple, replaced atomically


def _bot_configs() -> dict[str, dict]:
    """All bot configs, cached on the directory's (name, mtime) signature."""
    files = sorted((CONFIG_DIR / "bots").glob("*.yaml"))
    sig = tuple((p.name, p.stat().st_mtime) for p in files)
    hit = _cfg_cache.get("hit")
    if hit is None or hit[0] != sig:
        # Parse fully before swapping: the server is multi-threaded and a
        # half-populated cache would be visible to concurrent requests.
        data = {}
        for p in files:
            try:
                data[p.stem] = yaml.safe_load(p.read_text()) or {}
            except Exception as e:  # noqa: BLE001  one bad file must not blank the page
                log.warning("cannot parse %s: %s", p.name, e)
        hit = (sig, data)
        _cfg_cache["hit"] = hit
    return hit[1]


def _bots() -> list[dict]:
    out = []
    for stem, cfg in _bot_configs().items():
        row = {k: cfg.get(k) for k in ("name", "strategy", "symbol", "timeframe", "stage")}
        row["name"] = row["name"] or stem
        row["stage"] = row["stage"] or "research"
        out.append(row)
    return out


def _stage_of(name: str) -> str:
    for b in _bots():
        if b["name"] == name:
            return b["stage"]
    return ""


def _dash_cfg() -> dict:
    return load_system_config().get("dashboard", {}) or {}


def _universe() -> list[str]:
    """Symbols offered in the market tab: dashboard.symbols, else the risk
    whitelist, plus every bot's own symbol."""
    sys_cfg = load_system_config()
    base = _dash_cfg().get("symbols") or sys_cfg.get("risk", {}).get("allowed_symbols") \
        or DEFAULT_UNIVERSE
    seen = list(dict.fromkeys(base))
    for b in _bots():
        s = b.get("symbol")
        if isinstance(s, str) and s.endswith("/USDT") and s not in seen:
            seen.append(s)
    return seen


def _benchmark() -> str:
    return _dash_cfg().get("benchmark") or _universe()[0]


# ---------------------------------------------------------------- ledgers ---

def _live_db() -> Path:
    return storage_dir() / "live" / "live.sqlite"


def _open_logs() -> tuple:
    """Return (paper TradeLog, live TradeLog or None if no live ledger exists).

    Both are opened read-only: the dashboard never creates or writes a ledger
    (a missing paper ledger simply reads as empty)."""
    paper = TradeLog(storage_dir() / "trades.sqlite", read_only=True)
    lp = _live_db()
    live = TradeLog(lp, read_only=True) if lp.exists() else None
    return paper, live


def _close(*logs) -> None:
    for t in logs:
        if t is not None:
            t.close()


def _log_for(stage: str, paper, live):
    """Live bots read the live ledger; everything else reads the paper ledger."""
    return live if (stage == "live" and live is not None) else paper


def _capital_events() -> dict[tuple[str, str, str], float]:
    """Optional deposit/withdrawal ledger: config/capital_events.yaml.

    Format:
        events:
          - {date: YYYY-MM-DD, bot: my-bot, ledger: live, delta: 100}

    Capital moves show up as equity jumps; without this they would be counted
    as PnL. Missing file means no events (the default).
    """
    p = CONFIG_DIR / "capital_events.yaml"
    if not p.exists():
        return {}
    try:
        doc = yaml.safe_load(p.read_text()) or {}
    except Exception:  # noqa: BLE001  a broken ledger must not break the dashboard
        log.warning("capital_events.yaml could not be parsed; ignoring it")
        return {}
    out: dict[tuple[str, str, str], float] = {}
    for e in doc.get("events") or []:
        try:
            out[(str(e["date"]), str(e["bot"]), str(e.get("ledger", "live")))] = float(e["delta"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _daily_close(eq: pd.DataFrame) -> pd.Series:
    e = eq.sort_values("ts").copy()
    e["d"] = pd.to_datetime(e["ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    return e.groupby("d")["equity"].last()


def _twr(eq: pd.DataFrame, name: str, ledger: str) -> tuple[float | None, float | None]:
    """Time-weighted return and max drawdown with capital events removed.

    Daily closes are chained; each day's change has that day's deposit or
    withdrawal subtracted, so funding a bot is never reported as a return.
    """
    if eq is None or len(eq) < 2:
        return None, None
    close = _daily_close(eq)
    if len(close) < 2:
        return None, None
    cap = _capital_events()
    curve = [1.0]
    prev = float(close.iloc[0])
    for d, v in list(close.items())[1:]:
        v = float(v)
        delta = cap.get((d, name, ledger), 0.0)
        curve.append(curve[-1] * (1 + ((v - delta) - prev) / prev) if prev > 0 else curve[-1])
        prev = v          # next day's base is the actual equity, deposit included
    ser = pd.Series(curve)
    dd = float((1 - ser / ser.cummax()).max())
    return float(ser.iloc[-1] - 1), dd


# ---------------------------------------------------------------- market ---

_ticker_cache: dict = {"ts": 0.0, "data": []}


def _tickers() -> list[dict]:
    """24h tickers from the public spot endpoint, cached for 30 s."""
    if time.time() - _ticker_cache["ts"] < 30 and _ticker_cache["data"]:
        return _ticker_cache["data"]
    uni = _universe()
    try:
        rows = _ex().public_get_ticker_24hr(
            {"symbols": json.dumps([_spot_id(s) for s in uni], separators=(",", ":"))})
        raw = {r["symbol"]: r for r in rows}
        f = lambda v: float(v) if v is not None else None  # noqa: E731
        data = [
            {
                "symbol": s,
                "price": f(raw[_spot_id(s)]["lastPrice"]),
                "change_pct": f(raw[_spot_id(s)].get("priceChangePercent")),
                "high": f(raw[_spot_id(s)].get("highPrice")),
                "low": f(raw[_spot_id(s)].get("lowPrice")),
            }
            for s in uni if _spot_id(s) in raw
        ]
        _ticker_cache.update(ts=time.time(), data=data)
    except Exception as e:  # noqa: BLE001
        log.warning("ticker refresh failed: %s", e)
    return _ticker_cache["data"]


# Live kline tail: parquet history plus the most recent bars (including the
# still-open one) from the exchange. 20 s TTL so frontend polling cannot
# hammer the API.
_kline_cache: dict = {}


def _live_tail(symbol: str, tf: str, limit: int = 50) -> pd.DataFrame:
    key = (symbol, tf, limit)
    hit = _kline_cache.get(key)
    if hit and time.time() - hit["at"] < 20:
        return hit["df"]
    kl = _ex().public_get_klines({"symbol": _spot_id(symbol), "interval": tf, "limit": limit})
    raw = [[int(k[0]), *(float(x) for x in k[1:6])] for k in kl]
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    _kline_cache[key] = {"at": time.time(), "df": df}
    return df


# ------------------------------------------------------------------- API ---

def api_overview(_q) -> dict:
    paper, live = _open_logs()
    bots = []
    try:
        for b in _bots():
            tlog = _log_for(b["stage"], paper, live)
            ledger = "live" if tlog is live else "paper"
            b["ledger"] = ledger
            eq = tlog.equity_df(b["name"])
            fills = tlog.fills_df(b["name"])
            if len(eq):
                last = eq.iloc[-1]
                initial = float(eq["equity"].iloc[0])
                peak = float(eq["equity"].cummax().iloc[-1])
                twr, twr_dd = _twr(eq, b["name"], ledger)
                eq_now = float(last["equity"])
                pos_val = float(last["position_qty"] * last["last_price"])
                b.update(
                    equity=round(eq_now, 2),
                    cash=round(float(last["cash"]), 2),
                    position_qty=float(last["position_qty"]),
                    position_value=round(pos_val, 2),
                    pnl=round(eq_now - initial, 2),
                    pnl_pct=round(twr * 100, 2) if twr is not None
                    else round((eq_now / initial - 1) * 100, 2) if initial else None,
                    drawdown_pct=round(twr_dd * 100, 2) if twr_dd is not None
                    else round((1 - eq_now / peak) * 100, 2) if peak else 0.0,
                    realized_pnl=round(float(fills["realized_pnl"].sum()), 2) if len(fills) else 0.0,
                    fees=round(float(fills["fee"].sum()), 2) if len(fills) else 0.0,
                    n_fills=int(len(fills)),
                    last_ts=int(last["ts"]),
                    holding=abs(float(last["position_qty"])) > 1e-12,
                )
            # Heartbeat: is the process polling right now? Green window is
            # twice the timeframe's poll interval plus a grace period.
            hb = last_beat(b["name"])
            if hb is not None:
                age = time.time() * 1000 - hb
                alive_window = (base_interval(b.get("timeframe") or "1h") * 2 + 60) * 1000
                b["heartbeat_ts"] = hb
                b["alive"] = age <= alive_window
            else:
                b["heartbeat_ts"] = None
                b["alive"] = None   # never started, or not a running stage
            bots.append(b)
    finally:
        _close(paper, live)
    return {"bots": bots, "universe": _universe(), "benchmark": _benchmark()}


def api_live_summary(_q) -> dict:
    """Top-of-page KPIs for the live view, aggregated from the live ledger.

    Day/week changes are computed per bot first and then summed, never as a
    difference of the summed total: bots starting or stopping would otherwise
    show up as PnL. Capital events are subtracted.
    """
    names = [b["name"] for b in _bots() if b["stage"] == "live"]
    if not names or not _live_db().exists():
        return {"available": False}
    tlog = TradeLog(_live_db(), read_only=True)
    try:
        cap = _capital_events()
        total = notional = realized = fees = 0.0
        day_chg = week_chg = day_base = week_base = 0.0
        max_dd = 0.0
        n_holding = n_with_data = 0
        now = pd.Timestamp.now(tz="UTC")
        today = now.strftime("%Y-%m-%d")
        week_ago = (now - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        for n in names:
            eq = tlog.equity_df(n)
            fills = tlog.fills_df(n)
            if len(fills):
                realized += float(fills["realized_pnl"].sum())
                fees += float(fills["fee"].sum())
            if not len(eq):
                continue
            n_with_data += 1
            last = eq.iloc[-1]
            cur = float(last["equity"])
            total += cur
            pv = abs(float(last["position_qty"] * last["last_price"]))
            notional += pv
            n_holding += int(pv > 1e-9)
            _, dd = _twr(eq, n, "live")
            if dd is not None:
                max_dd = max(max_dd, dd)
            close = _daily_close(eq)
            prev = close[close.index < today]
            if len(prev):
                base = float(prev.iloc[-1])
                day_chg += cur - base - cap.get((today, n, "live"), 0.0)
                day_base += base
            older = close[close.index <= week_ago]
            if len(older):
                base = float(older.iloc[-1])
                moved = sum(v for (d, bot, lg), v in cap.items()
                            if bot == n and lg == "live" and week_ago < d <= today)
                week_chg += cur - base - moved
                week_base += base
    finally:
        tlog.close()
    if not n_with_data:
        return {"available": False}
    return {
        "available": True,
        "total": round(total, 2),
        "notional": round(notional, 2),
        "realized": round(realized, 4),
        "fees": round(fees, 4),
        "day_chg": round(day_chg / day_base, 5) if day_base else None,
        "week_chg": round(week_chg / week_base, 5) if week_base else None,
        "drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_limit": load_system_config().get("risk", {}).get("max_drawdown_pct"),
        "n_holding": n_holding,
        "n_bots": len(names),
    }


def api_markets(_q) -> dict:
    return {"tickers": _tickers()}


_SENT_LABELS = {
    # key: (label, description, data source, event-driven)
    "fear_greed": ("Fear & Greed Index", "broad market mood", "alternative.me", False),
    "funding": ("Funding rate", "long/short crowding", "Binance public API", False),
    "long_short": ("Long/short account ratio", "retail positioning", "Binance public API", False),
    "news": ("News RSS", "headlines, event-driven", "RSS feeds", True),
    "reddit": ("Reddit", "community mood, event-driven", "Reddit", True),
}


def api_sentiment(_q) -> dict:
    """Sentiment composite, if the optional sentiment pipeline has produced one."""
    try:
        from sentiment.composite import SOURCE_SPEC, load_config, regime
        from sentiment.store import load_composite, load_source
    except Exception:  # noqa: BLE001  sentiment is optional
        return {"available": False}
    comp = load_composite()
    if comp is None or comp.empty:
        return {"available": False}
    cfg = load_config()
    tail = comp.tail(120)
    latest = comp.iloc[-1]

    def has_z(key):
        zcol = f"z_{key}"
        return zcol in comp.columns and pd.notna(latest.get(zcol))

    active_w = sum(cfg.weight(k) for k in cfg.sources if has_z(k)) or 1.0
    sources = []
    for key, spec in SOURCE_SPEC.items():
        enabled = key in cfg.sources
        has = enabled and has_z(key)
        label, desc, origin, breaking = _SENT_LABELS.get(key, (key, "", key, False))
        value = None
        if key == "fear_greed" and enabled:
            raw = load_source("fear_greed")
            if raw is not None and len(raw):
                value = float(raw["value"].iloc[-1])
        sources.append({
            "key": key, "label": label, "desc": desc, "source": origin,
            "enabled": enabled, "weight": cfg.weight(key) if enabled else 0.0,
            "share": round(cfg.weight(key) / active_w, 4) if has else 0.0,
            "shift": spec.get("shift", 0),
            "z": round(float(latest[f"z_{key}"]), 2) if has else None,
            "value": value, "active": bool(has), "breaking": breaking,
        })
    return {
        "available": True,
        "score": round(float(latest["score"]), 1),
        "regime": regime(float(latest["score"]), cfg),
        "cutoffs": {"fear": cfg.extreme_fear, "greed": cfg.extreme_greed},
        "config": {"z_window": cfg.z_window, "ema_span": cfg.ema_span,
                   "sources": list(cfg.sources)},
        "n_sources": int(latest["n_sources"]),
        "divergence": round(float(latest["divergence"]), 2),
        "z": round(float(latest["z"]), 2),
        "sources": sources,
        "series": [{"ts": int(r.ts), "score": round(float(r.score), 1)}
                   for r in tail.itertuples(index=False)],
    }


def api_daily_pnl(q) -> dict:
    """Daily PnL bars: change in daily closing equity (capital events removed)
    plus realized PnL booked that day.

    Differences are taken per bot and then summed across bots. If a snapshot
    is missing for a day the next change covers the whole gap and is booked on
    the day it was observed; no intermediate points are invented.

    Params: bot=<name> for one bot; scope=live|paper to restrict the ledger.
    """
    name = q.get("bot", [""])[0]
    scope = q.get("scope", [""])[0]
    paper, live = _open_logs()
    try:
        if name:
            names = [name]
        else:
            names = [b["name"] for b in _bots()
                     if not scope or (b["stage"] == "live") == (scope == "live")]
        cap = _capital_events()
        chg: dict[str, float] = {}
        real: dict[str, float] = {}
        equity: dict[str, float] = {}
        capital: dict[str, float] = {}
        for n in names:
            tlog = _log_for(_stage_of(n), paper, live)
            ledger = "live" if tlog is live else "paper"
            eq = tlog.equity_df(n)
            if len(eq):
                close = _daily_close(eq)
                for d, v in close.diff().dropna().items():
                    delta = cap.get((d, n, ledger), 0.0)
                    if delta:
                        capital[d] = capital.get(d, 0.0) + delta
                    chg[d] = chg.get(d, 0.0) + float(v) - delta
                for d, v in close.items():
                    equity[d] = equity.get(d, 0.0) + float(v)
            f = tlog.fills_df(n)
            if len(f):
                ff = f.copy()
                ff["d"] = pd.to_datetime(ff["ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
                for d, v in ff.groupby("d")["realized_pnl"].sum().items():
                    real[d] = real.get(d, 0.0) + float(v)
    finally:
        _close(paper, live)
    days = sorted(set(chg) | set(real))
    series = [{"date": d,
               "pnl": round(chg.get(d, 0.0), 4),
               "realized": round(real.get(d, 0.0), 4),
               "capital": round(capital[d], 2) if d in capital else None,
               "equity": round(equity[d], 2) if d in equity else None}
              for d in days]
    wins = [s for s in series if s["pnl"] > 0]
    losses = [s for s in series if s["pnl"] < 0]
    return {
        "series": series,
        "stats": {
            "days": len(series),
            "win_days": len(wins),
            "loss_days": len(losses),
            # flat days (no PnL change) are excluded from the win rate
            "win_rate": round(len(wins) / (len(wins) + len(losses)) * 100, 1) if wins or losses else 0.0,
            "best": round(max((s["pnl"] for s in series), default=0.0), 4),
            "worst": round(min((s["pnl"] for s in series), default=0.0), 4),
            "sum_pnl": round(sum(s["pnl"] for s in series), 4),
            "sum_realized": round(sum(s["realized"] for s in series), 4),
        },
    }


def api_equity(q) -> dict:
    """Bot equity curve vs benchmark buy-and-hold, both rebased to 100."""
    name = q.get("bot", [""])[0]
    paper, live = _open_logs()
    try:
        eq = _log_for(_stage_of(name), paper, live).equity_df(name)
    finally:
        _close(paper, live)
    if eq.empty:
        return {"series": []}
    bench_sym = _benchmark()
    eq = eq[["ts", "equity"]].sort_values("ts")
    try:
        bench = load_bars(bench_sym, "1h")[["ts", "close"]].sort_values("ts")
        merged = pd.merge_asof(eq, bench, on="ts", direction="backward")
    except FileNotFoundError:
        merged = eq.assign(close=float("nan"))
    merged = merged.dropna(subset=["equity"])
    if merged.empty:
        return {"series": []}
    eq0 = float(merged["equity"].iloc[0])
    px = merged["close"].dropna()
    px0 = float(px.iloc[0]) if len(px) else None
    return {
        "benchmark": bench_sym,
        "series": [
            {
                "ts": int(r.ts),
                "equity": round(float(r.equity) / eq0 * 100, 3) if eq0 else None,
                "bench": round(float(r.close) / px0 * 100, 3)
                if px0 and pd.notna(r.close) else None,
            }
            for r in merged.itertuples(index=False)
        ],
    }


def api_klines(q) -> dict:
    """Klines plus fill markers for the selected bot.

    Bars are [ts, open, close, low, high, volume] (ECharts candlestick order).
    Parquet history is merged with a live tail from the exchange; the last
    bar is the still-open one.
    """
    symbol = q.get("symbol", [""])[0] or _benchmark()
    if symbol not in _universe():
        return {"error": "symbol not in the dashboard universe"}
    tf = q.get("tf", ["1h"])[0]
    if tf not in ALLOWED_TF:
        return {"error": "tf must be one of " + "/".join(ALLOWED_TF)}
    limit = kline_limit(q.get("limit", ["500"])[0])
    try:
        bars = load_bars(symbol, tf)
    except FileNotFoundError:
        bars = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    try:
        tail = _live_tail(symbol, tf, limit=max(50, min(limit, 1000)) if bars.empty else 50)
        if len(tail):
            bars = bars[bars["ts"] < int(tail["ts"].iloc[0])]
            bars = pd.concat([bars, tail], ignore_index=True) if len(bars) else tail
    except Exception as e:  # noqa: BLE001  fall back to parquet only
        log.warning("live kline fetch failed %s %s: %s", symbol, tf, e)
    if bars.empty:
        return {"error": f"no data for {symbol} {tf}"}
    bars = bars.tail(limit)
    name = q.get("bot", [""])[0]
    markers = []
    if name:
        paper, live = _open_logs()
        try:
            fills = _log_for(_stage_of(name), paper, live).fills_df(name)
        finally:
            _close(paper, live)
        if len(fills):
            window = fills[(fills["ts"] >= int(bars["ts"].iloc[0])) & (fills["symbol"] == symbol)]
            markers = [
                {"ts": int(r.ts), "side": r.side, "price": float(r.price), "qty": float(r.qty)}
                for r in window.itertuples(index=False)
            ]
    return {
        "symbol": symbol,
        "tf": tf,
        "bars": [
            [int(r.ts), float(r.open), float(r.close), float(r.low), float(r.high), float(r.volume)]
            for r in bars.itertuples(index=False)
        ],
        "markers": markers,
    }


def kline_limit(raw) -> int:
    """Snap a requested bar count up to the nearest fixed bucket."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 500
    return next((b for b in KLINE_LIMITS if n <= b), KLINE_LIMITS[-1])


def _merged(getter, name: str, scope: str = ""):
    """Fills/events from the paper and live ledgers.

    name given -> the ledger matching that bot's stage; scope=live|paper ->
    only that ledger (so real-money and simulated rows never share a table);
    neither -> both ledgers merged.
    """
    paper, live = _open_logs()
    try:
        if name:
            return getter(_log_for(_stage_of(name), paper, live), name)
        if scope == "live":
            return getter(live, None) if live is not None else getter(paper, None).iloc[0:0]
        if scope == "paper":
            return getter(paper, None)
        frames = [getter(paper, None)]
        if live is not None:
            frames.append(getter(live, None))
        frames = [f for f in frames if len(f)]
        if not frames:
            return getter(paper, None)
        return pd.concat(frames, ignore_index=True).sort_values("ts")
    finally:
        _close(paper, live)


def api_fills(q) -> dict:
    fills = _merged(lambda t, n: t.fills_df(n), q.get("bot", [""])[0], q.get("scope", [""])[0])
    return {"fills": fills.tail(100).to_dict("records")}


def api_events(q) -> dict:
    events = _merged(lambda t, n: t.events_df(n), q.get("bot", [""])[0],
                     q.get("scope", [""])[0]).tail(60)
    return {"events": events.to_dict("records")}


def _pnl_series(eq: pd.DataFrame) -> dict:
    """Compress an equity series into a PnL sparkline (<=120 points)."""
    if eq is None or eq.empty:
        return {"start": None, "last": 0.0, "last_pct": 0.0, "points": []}
    eq = eq.sort_values("ts")
    if len(eq) > 120:
        eq = eq.iloc[:: max(1, len(eq) // 120)]
    start = float(eq["equity"].iloc[0])
    pts = [{"ts": int(r.ts), "pnl": round(float(r.equity) - start, 4)}
           for r in eq.itertuples(index=False)]
    last = pts[-1]["pnl"]
    return {"start": round(start, 2), "last": last,
            "last_pct": round(last / start * 100, 2) if start else 0.0, "points": pts}


def api_strategy(_q) -> dict:
    """Per-bot strategy description, parameters, latest internal state and
    triggers, plus the global risk limits every order is subject to."""
    sys_cfg = load_system_config()
    paper, live = _open_logs()
    bots = []
    try:
        for b in _bots():
            cls = STRATEGIES.get(b["strategy"])
            doc = ""
            if cls is not None:
                mod = sys.modules.get(cls.__module__)
                doc = ((mod.__doc__ if mod else None) or cls.__doc__ or "").strip()
            tlog = _log_for(b["stage"], paper, live)
            bots.append({**b, "rules": doc,
                         "params": _bot_configs().get(b["name"], {}).get("params", {}),
                         "live": tlog.latest_strategy_state(b["name"]),
                         "ledger": "live" if tlog is live else "paper",
                         "pnl": _pnl_series(tlog.equity_df(b["name"]))})
    finally:
        _close(paper, live)

    sent = api_sentiment(None)
    risk = sys_cfg.get("risk", {})
    fees = sys_cfg.get("fees", {})

    def pct(k):
        return f"{risk[k]:.0%}" if k in risk else "n/a"

    factors = {
        "sentiment": {k: sent[k] for k in ("score", "regime", "n_sources")}
        if sent.get("available") else None,
        "risk": {
            "Max order notional": f'{risk.get("max_order_notional", "n/a")} USDT',
            "Min order notional": f'{risk.get("min_order_notional", "n/a")} USDT',
            "Max position / equity": pct("max_position_pct"),
            "Daily loss limit": f'{pct("daily_loss_pct")} -> no new entries today',
            "Max drawdown": f'{pct("max_drawdown_pct")} -> flatten and halt',
            "Order rate": f'{risk.get("max_orders_per_minute", "n/a")} per bar',
        },
        "universe": risk.get("allowed_symbols", []),
        "fees": f'taker {fees.get("taker_bps", 0) / 100}% + slippage '
                f'{sys_cfg.get("slippage_bps", 0) / 100}%',
    }
    return {"bots": bots, "factors": factors}


def _tail_lines(path: Path, max_bytes: int = ALERTS_TAIL_BYTES) -> list[str]:
    """Last lines of a file, reading at most max_bytes from its end."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        start = max(0, size - max_bytes)
        f.seek(start)
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]          # first line is probably cut in half
    return lines


def api_alerts(_q) -> dict:
    """Alert feed from storage/alerts.jsonl (latest first).

    Identical texts are merged into one row with a count and the latest
    timestamp, so a crash-looping process cannot bury other alerts.
    """
    path = storage_dir() / "alerts.jsonl"
    items: list[dict] = []
    if path.is_file():
        for line in _tail_lines(path)[-200:]:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    items.reverse()
    by_text: dict[str, dict] = {}
    for it in items:
        t = it.get("text", "")
        if t in by_text:
            by_text[t]["count"] += 1
        else:
            by_text[t] = {**it, "count": 1}
    merged = sorted(by_text.values(), key=lambda x: x.get("ts", 0), reverse=True)
    return {"alerts": merged[:40]}


ROUTES = {
    "/api/overview": api_overview,
    "/api/live_summary": api_live_summary,
    "/api/markets": api_markets,
    "/api/sentiment": api_sentiment,
    "/api/equity": api_equity,
    "/api/daily_pnl": api_daily_pnl,
    "/api/klines": api_klines,
    "/api/fills": api_fills,
    "/api/events": api_events,
    "/api/strategy": api_strategy,
    "/api/alerts": api_alerts,
}


def _clean(obj):
    """Replace NaN/Infinity with None (recursively) so the JSON is strict."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def _json_default(obj):
    """Fallback for non-JSON types (numpy arrays/scalars, timestamps, pd.NA/NaT)."""
    if hasattr(obj, "tolist") and getattr(obj, "ndim", 0):  # numpy array (ndim >= 1)
        try:
            return _clean(obj.tolist())
        except (TypeError, ValueError):
            return str(obj)
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(obj, "item"):           # numpy scalar
        try:
            return _clean(obj.item())
        except (TypeError, ValueError):
            pass
    return str(obj)


def to_json(payload) -> bytes:
    """Strict JSON: NaN/Infinity become null; allow_nan=False guarantees it."""
    return json.dumps(_clean(payload), default=_json_default, allow_nan=False).encode()


def allowed_hosts(port: int) -> frozenset[str]:
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})


class Handler(BaseHTTPRequestHandler):
    # Socket timeout per connection: a client that opens a socket and never
    # finishes its request cannot pin a worker thread forever.
    timeout = REQUEST_TIMEOUT_S

    def log_message(self, fmt, *args):  # silence the access log
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client went away (page refresh/close); not an error

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in allowed_hosts(self.server.server_address[1])

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, b'{"error":"forbidden host"}', "application/json")
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            html = (STATIC_DIR / "dashboard.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        asset = STATIC_FILES.get(parsed.path)
        if asset is not None:
            path = STATIC_DIR / asset[0]
            if not path.is_file():
                return self._send(404, b'{"error":"not found"}', "application/json")
            return self._send(200, path.read_bytes(), asset[1])
        handler = ROUTES.get(parsed.path)
        if handler is None:
            return self._send(404, b'{"error":"not found"}', "application/json")
        try:
            payload = to_json(handler(parse_qs(parsed.query)))
            return self._send(200, payload, "application/json")
        except Exception:  # noqa: BLE001
            log.error("API %s failed:\n%s", parsed.path, traceback.format_exc())
            return self._send(500, b'{"error":"internal"}', "application/json")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Trident local monitoring dashboard")
    ap.add_argument("--port", type=int, default=None,
                    help="port to listen on (default: dashboard.port in system.yaml, else 8777)")
    args = ap.parse_args(argv)
    port = args.port or _dash_cfg().get("port", 8777)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    log.info("dashboard running at http://127.0.0.1:%d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
