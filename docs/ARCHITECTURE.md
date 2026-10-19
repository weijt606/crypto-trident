# Trident architecture

## Design rules

These rules are what make backtest results comparable to live behavior. Changes that break
them should not be merged.

1. **Strategy code is shared across environments.** Nothing under `strategies/` may branch on
   backtest / paper / live. Environment differences live in the execution layer only.
2. **The risk guard cannot be bypassed.** Every order path calls
   `safety.guard.RiskGuard.check_order`, with the sanctioned exceptions listed under
   [Risk guard](#risk-guard). `revive()` is for a human operator only.
3. **Secrets live only in `.env`.** Never in code, configs or git. `.env` is read by
   `core.config` but not exported into the process environment. Only the live executors and
   `scripts/verify_trade_key.py` read the trade key; data, paper and dashboard use public
   endpoints or a read-only key.
4. **Honest backtests.** A signal is produced at the close of bar *t* and filled at the open of
   bar *t+1*. Fees and slippage are always charged.
5. **Time comes from market data.** Strategies and risk checks use bar timestamps, never the
   system clock, so backtest and live behave identically.
6. **Out-of-sample is the promotion gate.** Every backtest report contains in-sample and
   out-of-sample segments.
7. **No sentiment lookahead.** Strategies read sentiment only via `self.sentiment`, injected by
   the engine according to each data point's availability timestamp. A sentiment indicator
   should pass `scripts/validate_sentiment.py` (|IC| >= 0.03, monotonic quantiles) before a
   strategy trades on it.

## Data flow

```
             ┌──────────────── config/system.yaml + config/bots/<bot>.yaml ───────────────┐
             ▼                                                                            ▼
 exchange ─▶ data/ (fetcher, Parquet bars) ─▶ Strategy.on_bar() ─▶ Signal ─▶ RiskGuard ─▶ executor
 (ccxt)                         ▲                                                     │
                                │ sentiment / funding injected (no lookahead)         │
                         sentiment/ (optional)                                        ▼
                                                     data/store TradeLog (SQLite): fills, events, equity
                                                                                      │
                                                     monitor/ dashboard, health, alerts, reports
```

Executors by environment:

| Environment | Entry point | Executor | Ledger |
|---|---|---|---|
| Backtest | `scripts/run_backtest.py` | `backtest/engine.py` | in-memory, report to `storage/reports/` |
| Paper | `scripts/run_paper.py` | `execution/paper.py` | `storage/trades.sqlite` |
| Live spot | `scripts/run_live.py` | `execution/live.py` | `storage/live/live.sqlite` |
| Live perp | `scripts/run_live_perp.py` | `execution/live_perp.py` | `storage/live/live.sqlite` |

## Bot lifecycle

`research` (backtest only) → `paper` → `live` → `retired`, stored as `stage:` in the bot yaml.
The live executors refuse to start unless `stage: live` and `risk.allowed_symbols` is set and
non-empty. Promoting a bot to live is a human decision: automation must never edit the stage.

## Risk guard

`safety/guard.py` is shared by all environments:

| Check | Default (`config/system.yaml` → `risk:`) | Action |
|---|---|---|
| Symbol whitelist | `allowed_symbols` | reject |
| Max order notional | `max_order_notional` | reject |
| Max position / equity | `max_position_pct` (× leverage) | reject |
| Short selling / leverage | spot: no shorts; perp: leverage hard cap 3× | reject |
| Min order notional | `min_order_notional` | reject |
| Order rate | `max_orders_per_minute`, counted over 60 s of bar timestamps (orders carry their bar's time, so for 1m+ bars it caps orders per bar) | reject |
| Daily loss (HALT) | `daily_loss_pct` | blocks any order that increases exposure or flips the position sign until the next UTC day; reducing allowed |
| Max drawdown (KILL) | `max_drawdown_pct` from peak (live: clamped to <= 0.30) | flatten, stop, write kill file; manual `revive.py` |
| Bad numbers | non-finite / <= 0 qty or price, non-finite equity or position, opening with equity <= 0 | reject |

`RiskConfig` validates itself on construction (finite values, `0 < daily_loss_pct,
max_drawdown_pct < 1`, `0 < max_position_pct <= 1`, `max_leverage >= 1`, notional limits
> 0), so a typo such as `NaN` or `15` instead of `0.15` cannot silently disable a breaker.
A non-finite equity mark is ignored: it never moves the peak and never clears a breaker.

Sanctioned exceptions to `check_order` (all can only reduce exposure):

- **Kill / soft-cut / retire flatten.** Once the drawdown kill fires the guard rejects every
  order, yet the liquidation must still go out. The spot executor's kill path and the perp
  executor's `_flatten_and_persist` (used by kill, `.softcut` and `retire_live_perp.py`) place
  closing orders directly, then verify against the exchange and persist the final state.
  The spot liquidation sells the free base balance above the baseline as just read from
  the exchange (rounded down to precision), not the local books.
- **Reduce-only closes.** Before a perp flatten the exchange position is re-read and is what
  gets closed (nothing is sent if it is already flat). In one-way mode the close is
  `reduceOnly` and capped at min(|bot position|, |exchange position|); the only plain
  (non-reduce-only) close is the explicit baseline case, where the bot's own position
  (confirmed by the local books) offsets a pre-existing position that is not the bot's.
  If instead the baseline was closed outside the bot (exchange flat, or same sign as the
  baseline but smaller, and the books disagree), nothing is sent for it: the baseline is
  reset and a CRITICAL alert/event is raised. Hedge mode uses
  `positionSide`-bound legs. A remainder below the exchange's amount precision is treated as
  flat and re-verified against the exchange.

No other path may place an order without `check_order`.

Live executors add their own defenses on top:

- **Persistent breakers.** `RiskGuard.export_state()` / `load_state()` carry the drawdown
  peak, the day-start equity, an active daily HALT and the KILL flag in
  `<live_dir>/<bot>_state.json`; a restart neither resets the peak nor clears a HALT, and a
  recorded kill refuses to start even if the `.kill` marker was deleted by hand.
  `scripts/revive.py` clears both and sets the peak and the day-start equity to
  re-baseline at the next mark, so a same-day revive does not re-trip HALT (works for spot
  and perp state files).
- **Spot balances.** A buy whose fee is charged in the base asset books filled - fee; a
  fee missing from the order response is fetched from the order's trades, else estimated
  from `fee_bps` and logged. Every sell is capped at the free base balance minus the
  baseline (rounded down to precision); a leftover below `min_order_notional` is treated
  as flat (moved to the baseline). Non-finite balances, fills, fees or funding amounts are
  skipped/alerted and never reach the books or the state file.
- **Single instance.** Each executor (and `retire_live_perp.py` / `revive.py`) takes a
  non-blocking `flock` on `<live_dir>/<bot>.lock`; a second process refuses with exit 78.
- **Each bar decided once.** `last_bar_ts` is persisted before the first order of a bar;
  the strategy is never re-run for a bar that was already decided.
- **Client order ids.** Orders are never re-sent for a decided bar; a timed-out send is
  resolved by client-id lookup. Every order carries a deterministic client order id
  (`<bot>-<bar_ts>-<n>`, sanitized to Binance's 36-character rule); flatten orders add a
  persisted attempt counter so a re-run on the same bar (e.g. a repeated retire) never
  reuses an old id. An empty or async acknowledgement (`filled=0`, status `NEW`) is re-read
  with `fetch_order`.
- **Exchange-truth accounting.** Fills are booked as the exchange filled them (no local
  leverage-cap shrinking; partial fills alert). A position the books missed is adopted
  through the same signed accounting at the exchange entry price (when unambiguous) or the
  mark, so adoption never creates phantom PnL that could trip the kill.
- **Startup checks.** The trade key must have withdrawals, universal/internal transfer,
  margin borrowing and options disabled (same rules as `scripts/verify_trade_key.py`);
  a missing IP whitelist alerts. After 5 consecutive startup auth failures the executor
  halts with exit 78 instead of restart-looping; a successful start resets the counter.
- **Robust I/O.** State and marker files are written atomically (tmp + fsync + rename);
  exchange error text is redacted (URL query strings, signatures) before logs, events and
  alerts; clock re-sync and retry on `-1021`; a `.softcut` halt marker is used by
  `scripts/retire_live_perp.py` for controlled retirement.

## Sentiment (optional)

`sentiment/` fetches public sources, stores them under `storage/sentiment/`, and builds a daily
composite score in [-100, 100]: per-source daily mean, anti-lookahead shift, rolling z-score,
weighted blend, EMA, tanh. Every row carries `avail_ts`; executors inject a value only when
`avail_ts <= bar.ts`. Configuration is an optional `sentiment:` block in `config/system.yaml`
(`sources`, `weights`, `z_window`, `ema_span`, `regime_cutoffs`). Defaults are neutral: Fear &
Greed, funding and long/short ratio with equal weights, a 60-day window, EMA span 5 and cutoffs
at ±60. News RSS and Reddit are opt-in. `sentiment/validate.py` tests momentum (IC > 0) and
contrarian (IC < 0) hypotheses without assuming either.

## Storage layout (gitignored)

```
storage/
  bars/            Parquet OHLCV per symbol/timeframe
  trades.sqlite    paper ledger: fills, events, equity snapshots
  live/            live.sqlite ledger, executor state (incl. breaker state), kill / softcut
                   markers, single-instance .lock files, startup auth-failure counters
  heartbeats/      process heartbeats read by the health check
  sentiment/       raw source data, composite index, funding history
  reports/         backtest and sentiment-validation reports
  alerts.jsonl     alert history shown on the dashboard
logs/          trident.log, per-service logs under logs/services/
```

## Deployment

For unattended operation on a Linux server, `scripts/install_services_systemd.sh` installs
user-level systemd units: one paper unit per `stage: paper` bot, the dashboard, hourly health
and sentiment timers, and optionally one live unit (`--live <bot>`). The dashboard binds to
`127.0.0.1`, rejects requests whose `Host` header is not `127.0.0.1:<port>` or
`localhost:<port>`, sends a strict Content-Security-Policy and loads no third-party resources
(ECharts is vendored in `monitor/static/`). Use an SSH tunnel (`ssh -L 8777:127.0.0.1:8777 <server>`) to view it remotely.
