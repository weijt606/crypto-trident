# Trident architecture

## Design rules

These rules keep the strategy interface and accounting conventions consistent across environments.
They do not make simulated fills equivalent to live execution or establish strategy profitability.

1. **Strategy code is shared across environments.** Nothing under `strategies/` may branch on
   backtest / paper / live. Environment differences live in the execution layer only.
2. **Strategy orders must pass the risk guard.** Execution code calls
   `safety.guard.RiskGuard.check_order`, with documented emergency flatten exceptions under
   [Risk guard](#risk-guard). This is an implementation/contribution rule, not a sandbox
   against modified Python code. `scripts/revive.py` is for human-reviewed live recovery.
3. **Secrets stay out of source and shared configuration.** `core.config` reads an existing
   process environment first, then `.env`; file values are not exported into `os.environ`.
   Already-injected environment values retain normal process inheritance. Only live executors
   and `scripts/verify_trade_key.py` call `trade_keys()`. Public data, paper and dashboard
   requests need no API credentials. `read_only_keys()` is a helper reserved for integrations;
   the bundled scripts do not currently call it.
4. **Explicit simulation assumptions.** Ordinary strategy signals at bar *t* close enter a
   queue for simulated fills at bar *t+1* open. Emergency simulated flattening uses the current
   close. Configured fee/slippage rates are applied and can be zero. Paper fills use the
   latest closed bar's close; perpetual liquidation is simplified.
5. **Strategy evaluation and risk checks use bar time.** Polling, heartbeat, retry, auth-alert
   throttling and exchange request timestamps use wall-clock time. Similar strategy inputs do
   not guarantee identical behavior or outcomes in the three environments.
6. **Review out-of-sample behavior.** The backtest CLI defaults to a chronological 70/30 split,
   with a new strategy instance per segment. `--oos 0` disables the split. Reviewing a report
   is an operator responsibility; live startup does not validate a report or its metrics.
7. **Expose sentiment according to recorded availability.** `self.sentiment` is injected
   using `avail_ts <= bar.ts`. The timestamps include source-specific delay assumptions,
   not historical observations of every provider publication or revision.
   `scripts/validate_sentiment.py` reports IC and quantile returns for manual review; its
   |IC| threshold is a heuristic, not a statistical significance test or a live-start gate.

## Data flow

```
             ┌──────────────── config/system.yaml + config/bots/<bot>.yaml ───────────────┐
             ▼                                                                            ▼
 exchange ─▶ data/ (fetcher, Parquet bars) ─▶ Strategy.on_bar() ─▶ Signal ─▶ RiskGuard ─▶ executor
 (ccxt)                         ▲                                                     │
                                │ sentiment / funding by recorded availability        │
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

`research` → `paper` → `live` → `retired` is the intended workflow, stored as `stage:` in YAML.
Backtest and manual paper commands do not enforce the entire lifecycle; the service installer
uses the label to select paper units, and dashboard/health views also use it for grouping.
The live executors refuse to start unless `stage: live` and `risk.allowed_symbols` is set and
non-empty. Promoting a bot to live is a human decision: automation must never edit the stage.

## Risk guard

`safety/guard.py` is shared by all environments:

| Check | Default (`config/system.yaml` → `risk:`) | Action |
|---|---|---|
| Symbol whitelist | `allowed_symbols` | reject |
| Max order notional | `max_order_notional` | reject |
| Max position / equity | `max_position_pct` (× leverage) | reject |
| Short selling / leverage | spot defaults: no shorts, 1×; supplied perp CLI overlay caps max leverage at 3× | reject |
| Min order notional | `min_order_notional` | reject |
| Order rate | `max_orders_per_minute`, counted over 60 s of bar timestamps (orders carry their bar's time, so for 1m+ bars it caps orders per bar) | reject |
| Daily loss (HALT) | `daily_loss_pct` | blocks exposure increases or sign flips until the next UTC day; reductions still face other order checks |
| Max drawdown (KILL) | `max_drawdown_pct` from peak (supplied live CLI paths clamp to <= 0.30) | request flatten and stop; live persists kill state/marker for manual review and `revive.py` |
| Bad numbers | non-finite / <= 0 qty or price, non-finite equity or position, opening with equity <= 0 | reject |

`RiskConfig` validates finite values and ranges on construction: loss fractions are in
`(0, 1)`, `max_position_pct` is in `(0, 1]`, leverage is at least 1, maximum notional is
positive, and minimum notional lies between zero and the maximum. A typo such as `NaN` or
`15` instead of `0.15` is rejected.
A non-finite equity mark is ignored: it never moves the peak and never clears a breaker.

Checks run on new closed bars, not continuously. No exchange-hosted stop is installed.
Paper restores account snapshots/cost basis, but does not persist breaker state as live does.
A per-bot lock does not coordinate different bot names managing the same account and symbol.
The perpetual executor attempts margin/leverage read-back, but unsupported or failed reads
are logged and skipped; failed position-mode lookup assumes one-way mode. These are
current fallback behaviors, not proof that every exchange setting has been verified.

Sanctioned exceptions to `check_order` (intended to reduce the bot's booked exposure):

- **Kill / soft-cut / retire flatten.** Once the drawdown kill fires the guard rejects every
  order, yet the liquidation must still go out. The spot executor's kill path and the perp
  executor's `_flatten_and_persist` (used by kill, `.softcut` and `retire_live_perp.py`) place
  closing orders directly, then verify against the exchange and persist the final state.
  Spot flattening normally reads free base balance above the baseline and rounds down; if
  that read fails, it falls back to the booked position. A failed read or order can leave
  exposure that must be checked manually.
- **Reduce-only closes.** Before a perp flatten the exchange position is re-read and is what
  gets closed (nothing is sent if it is already flat). In one-way mode the close is
  `reduceOnly` and capped at min(|bot position|, |exchange position|); the only plain
  (non-reduce-only) close is the explicit baseline case, where the bot's own position
  (confirmed by the local books) offsets a pre-existing position that is not the bot's.
  If instead the baseline was closed outside the bot (exchange flat, or same sign as the
  baseline but smaller, and the books disagree), nothing is sent for it: the baseline is
  reset and a CRITICAL alert/event is raised. Hedge mode uses
  `positionSide`-bound legs. A remainder below the exchange's amount precision cannot be
  sent; the verification path re-reads it and may classify a below-precision residual as dust. Restoring a
  pre-existing baseline can increase the exchange's net position even while closing the
  bot's offsetting booked position; do not treat every flatten as exchange-level reduce-only.

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
  from `fee_bps` and logged. Sells attempt a cap at free base balance minus the baseline
  (rounded down); a failed balance read skips that free-balance cap. Strategy sells also
  cap at the booked position. A leftover below `min_order_notional` can be classified as
  dust and moved to the baseline; this does not mean the asset was sold. Non-finite balances, fills, fees or funding amounts are
  rejected, ignored or alerted on the corresponding read/booking paths. These checks do not
  replace independent reconciliation of account values.
- **Single instance.** Each executor (and `retire_live_perp.py` / `revive.py`) takes a
  non-blocking `flock` on `<live_dir>/<bot>.lock`; a second process refuses with exit 78.
- **Each bar decided once.** `last_bar_ts` is persisted before the first order of a bar;
  the strategy is not re-run for a recorded bar. A crash after this mark can also skip
  remaining signals; this is not a durable queue of pending order intents.
- **Client order ids.** Orders are never re-sent for a decided bar; a timed-out send is
  looked up by client id. An unsuccessful lookup does not distinguish a missing order from
  an unavailable lookup endpoint; this is not a full Unknown-state recovery protocol.
  Every order carries a deterministic client order id
  (`<bot>-<bar_ts>-<n>`, sanitized to Binance's 36-character rule); flatten orders add a
  persisted attempt counter so a re-run on the same bar (e.g. a repeated retire) never
  reuses an old id. An empty or async acknowledgement (`filled=0`, status `NEW`) is re-read
  with `fetch_order`.
- **Exchange-truth accounting.** Fills are booked as the exchange filled them (no local
  leverage-cap shrinking; partial fills alert). A position the books missed is adopted
  through the same signed accounting at the exchange entry price (when unambiguous) or the
  mark. This avoids treating a missed entry as a zero-cost position, but mark-based adoption
  cannot reconstruct an unknown historical entry price or all missed fees.
- **Startup checks.** The trade key must have withdrawals, universal/internal transfer,
  margin borrowing and options disabled (same rules as `scripts/verify_trade_key.py`);
  reading and the relevant trading permission must be enabled, IP restriction must be true,
  and missing/malformed required fields refuse startup. After 5 consecutive startup auth failures the executor
  halts with exit 78 instead of restart-looping; a successful start resets the counter.
- **Robust I/O.** State and marker files are written atomically (tmp + fsync + rename);
  exchange error text is redacted (URL query strings, signatures) before logs, events and
  alerts; `-1021` attempts a CCXT time-offset refresh before a later retry, not a guaranteed
  replay of the failed order. A `.softcut` halt marker is used by
  `scripts/retire_live_perp.py` for controlled retirement.

## Sentiment (optional)

`sentiment/` fetches public sources, stores them under `storage/sentiment/`, and builds a daily
composite score in [-100, 100]: per-source daily mean, anti-lookahead shift, rolling z-score,
weighted blend, EMA, tanh. Every row carries `avail_ts`; executors inject a value only when
`avail_ts <= bar.ts`. Configuration is an optional `sentiment:` block in `config/system.yaml`
(`sources`, `weights`, `z_window`, `ema_span`, `regime_cutoffs`). Defaults select Fear &
Greed, funding and long/short ratio with equal weights, a 60-day window, EMA span 5 and cutoffs
at ±60; equal weights do not establish that the index is unbiased. News RSS and Reddit
are opt-in; Reddit also requires an access acknowledgement and User-Agent.
`sentiment/validate.py` reports momentum (IC > 0) and contrarian (IC < 0) associations.
The IC report is not a runtime enforcement mechanism.

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
