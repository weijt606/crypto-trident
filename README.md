# Trident

**English** | [简体中文](README.zh-CN.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Exchange: Binance via ccxt](https://img.shields.io/badge/exchange-Binance%20%28ccxt%29-informational.svg)](https://github.com/ccxt/ccxt)
[![Not investment advice](https://img.shields.io/badge/not-investment%20advice-red.svg)](DISCLAIMER.md)

**One strategy codebase, three environments: backtest, paper, live.**
Repository and package name: `crypto-trident`.

*Why "Trident": one shaft, three prongs. One strategy codebase, three environments (backtest, paper, live).*

> [!WARNING]
> **A research and personal project. Not investment advice.**
> **研究与个人项目，不构成任何投资建议。**
> Crypto and crypto-derivatives trading is highly risky and you can lose all of your capital.
> Use at your own risk. Read the full [DISCLAIMER](DISCLAIMER.md) before using this software.
> 加密货币及衍生品交易风险极高，可能损失全部本金，使用风险自负。使用前请阅读完整[免责声明](DISCLAIMER.md)。

Trident is a small, opinionated crypto quant trading framework in Python. A strategy is a plain
class that consumes closed bars and emits signals. The same class runs unchanged in the
event-driven backtester, in a paper trader fed by live market data, and in live executors for
Binance spot and USDT-margined perpetuals. Every order, in every environment, passes through
the same independent risk guard.

> **Disclaimer.** Trident is infrastructure, not a trading strategy. It ships one textbook
> example (a 50/200 SMA crossover) to demonstrate the API; it is **not** expected to be
> profitable. Trading crypto assets carries substantial risk of loss. Nothing in this
> repository is financial advice. See the full [DISCLAIMER](DISCLAIMER.md).
>
> - **No warranty.** The software is provided "as is", without warranty of any kind; see
>   [LICENSE](LICENSE). You are solely responsible for any orders placed with it.
> - **Backtest and paper results do not predict live results.** Fills, latency, fees,
>   funding and market impact differ in live trading.
> - **Not affiliated with or endorsed by Binance or any exchange.** Exchange names are used
>   only to describe API compatibility.
> - **Exchange availability is geo-restricted** (for example, Binance is not available to US
>   persons). You must comply with the laws of your jurisdiction and with your exchange's
>   terms of service.

## Dashboard

![Dashboard: equity curve vs buy & hold, bot status and fills](docs/images/dashboard-equity.png)

![Dashboard: daily PnL, risk limits and strategy state](docs/images/dashboard-strategy.png)

> Screenshots use **fully synthetic demo data**: randomly generated prices labelled BTC/USDT and
> simulated trades of the bundled SMA example. The equity, PnL, win-rate and buy-and-hold figures
> are illustrative only. They are not a backtest, not real market history and not real or expected
> trading results, and say nothing about how the example strategy would perform.

## Features

- **Exchange interface** via [ccxt](https://github.com/ccxt/ccxt) (Binance spot + USDT-M perps):
  public market data, read-only account access, order placement with exchange precision and
  min-notional handling, hedge-mode aware perp orders, clock re-sync on timestamp errors,
  auth-failure detection that reports the outbound IP (masked in alerts, full IP in the
  local log only; looked up via `api.ipify.org`, only when an authentication failure occurs).
- **Data layer**: OHLCV fetcher, Parquet bar store, SQLite trade journal (fills, events, equity).
- **Backtester**: event-driven, signal at bar *t* close, fill at bar *t+1* open, fees and
  slippage always charged, in-sample / out-of-sample split in every report, perp margin and
  liquidation simulation.
- **Paper trading**: polls live bars, simulates fills with the backtester's cost model,
  resumable state, startup warmup that rebuilds indicator state without placing orders.
- **Live executors**: spot and perp, stage-gated (a human must set `stage: live`) and refusing
  to start without `risk.allowed_symbols`; reconcile with the exchange, persist on every fill,
  circuit-breaker kill file, controlled retirement script for perp bots
  (`scripts/retire_live_perp.py`) that flattens and halts.
- **Risk guard** (`safety/guard.py`): symbol whitelist, max order notional, max position,
  order rate limit, daily-loss halt, max-drawdown kill switch with manual revive. Strategies
  cannot disable it. Perp leverage is hard-capped at 3× by the risk overlay and the
  executors, whatever the config says.
- **Monitoring**: local web dashboard (bots, equity curves, klines with fill markers, fills,
  events, daily PnL, tickers, health; loopback only, read-only ledger access, strict CSP, no
  third-party resources), heartbeats,
  health checks, optional Telegram alerts.
- **Sentiment pipeline (optional)**: Fear & Greed index plus exchange funding and long/short
  ratio by default; news RSS and Reddit are opt-in. Availability timestamps prevent lookahead,
  and an IC + quantile validation gate tests momentum vs contrarian hypotheses.
- **Ops**: user-level systemd installer, pre-commit secret scanner.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/weijt606/crypto-trident.git && cd crypto-trident
uv sync
git config core.hooksPath scripts/githooks      # enable the secret scanner
uv run pytest                                    # run the test suite

# 1. fetch history (public endpoint, no key needed)
uv run python scripts/fetch_data.py --symbol BTC/USDT --timeframe 1h --days 730

# 2. backtest the example bot (config/bots/sma-demo.yaml)
uv run python scripts/run_backtest.py --bot sma-demo

# 3. paper trade it on live data
uv run python scripts/run_paper.py --bot sma-demo

# 4. watch it
uv run python -m monitor.dashboard               # http://127.0.0.1:8777 (--port to change)
uv run python scripts/report.py --bot sma-demo
```

The example bot needs a 200-bar warmup on 1h bars, so the backtest needs well over 200 bars of
history; 730 days is plenty.

Optional extras:

```bash
# sentiment pipeline (see "Data sources and terms" below)
uv run python scripts/fetch_sentiment.py --backfill
uv run python scripts/fetch_data.py --symbol BTC/USDT --timeframe 1d --days 2000   # validation needs 1d bars
uv run python scripts/validate_sentiment.py --symbol BTC/USDT
```

Perp backtests (`market: perp`) need a funding-rate history file:
`storage/sentiment/funding.parquet` for BTC (written by `fetch_sentiment.py`) or
`storage/sentiment/funding_<BASE>.parquet` for other coins.

## Writing a strategy

```python
# strategies/my_strategy.py
from core.models import AccountView, Bar, Side, Signal
from strategies.base import Strategy

class MyStrategy(Strategy):
    @classmethod
    def default_params(cls) -> dict:
        return {"alloc": 0.5}

    @property
    def warmup(self) -> int:
        return 50                      # bars needed before the first signal

    def on_bar(self, bar: Bar, account: AccountView) -> list[Signal]:
        # pure function of past bars + read-only account view:
        # no network, no clock, no environment checks
        if account.position_qty == 0 and bar.close > bar.open:
            return [Signal(Side.BUY, account.equity * self.params["alloc"] / bar.close, reason="up bar")]
        return []
```

Register it in `strategies/__init__.py` (`STRATEGIES`), then copy `config/bots/sma-demo.yaml`
to a new file, set `strategy:` and `params:`, and run it with `--bot <file name>`.

## Going live (read this first)

1. Paper trade long enough to trust the behavior, and review an out-of-sample backtest. This is
   a minimum engineering check, not evidence of future profitability.
2. Create a **dedicated** Binance API key: reading + trading only, **withdrawals disabled**,
   **IP whitelist** on, ideally in a sub-account holding only capital you can afford to lose.
3. Put keys in `.env` (copy `.env.example`, `chmod 600 .env`). Use a separate read-only key for
   data/monitoring; only the live executors and `verify_trade_key.py` read the trade key.
4. `uv run python scripts/verify_trade_key.py` to check permissions.
5. Set `live.cash_cap` / `initial_cash` in `config/system.yaml` (or `cash_cap` in the bot yaml for
   perps), review the `risk:` limits (including `allowed_symbols`, which live executors
   require), then **manually** change the bot's `stage` to `live`.
6. `uv run python scripts/run_live.py --bot <name>` (spot) or `uv run python scripts/run_live_perp.py --bot <name>` (perp).
7. If the kill switch fires, investigate, then `uv run python scripts/revive.py --bot <name> --reviewed "..."`.

Run only **one** live executor per bot at any time.

## Sentiment configuration

All keys are optional; add a `sentiment:` block to `config/system.yaml` to change them.

```yaml
sentiment:
  sources: [fear_greed, funding, long_short]   # default; add news and/or reddit to opt in
  weights: {}                                   # empty = equal weights across enabled sources
  z_window: 60                                  # rolling z-score window (days)
  ema_span: 5                                   # smoothing (days)
  regime_cutoffs: [-60, 60]                     # extreme fear / extreme greed labels
```

## Data sources and terms

Each data source has its own terms. You are responsible for complying with them.

- **Fear & Greed Index** by [alternative.me](https://alternative.me/crypto/fear-and-greed-index/):
  free to use with attribution: credit alternative.me right next to wherever you display the
  data (the dashboard does), and do not present it as your own service.
- **Exchange market data** (bars, tickers, funding rates, long/short ratio): public endpoints,
  subject to the exchange's API terms and rate limits.
- **Reddit** (opt-in): uses Reddit's public, unauthenticated RSS/JSON endpoints. Reddit's User
  Agreement, Data API Terms and Responsible Builder Policy apply, and Reddit may block or
  disallow unauthenticated automated access; enabling this source is at your own discretion.
  Set `TRIDENT_USER_AGENT` in `.env` to a descriptive value with contact info, e.g.
  `trident/0.1 (by u/yourname; you@example.com)`, and keep request rates low (hourly at most).
- **News RSS feeds** (opt-in): intended for personal use. Do not redistribute stored headlines
  (`storage/sentiment/news.parquet`).
- **api.ipify.org**: called only after an exchange authentication failure, to report your
  outbound IP so you can whitelist it.

## Configuration notes

- Defaults in `config/system.yaml` are deliberately small: backtest/paper `initial_cash: 1000`,
  `risk.max_order_notional: 1000`, live `cash_cap: 100`. Set them to match your own capital.
- Environment overrides: `TRIDENT_STORAGE_DIR`, `TRIDENT_LOG_DIR`, `TRIDENT_USER_AGENT`.
  Each may be set in the process environment or in `.env` (the process environment wins).
  Keys are read from `.env` on demand and are **not** exported into the process environment.
- Executors exit with code **78** when halted by the risk guard (kill switch, soft cut, failed
  startup safety check, `stage` not set to `live`). The systemd units set
  `RestartPreventExitStatus=78`, so a halted bot stays down until a human investigates.
  Startup authentication failures exit with code 1 (restartable) until 5 consecutive
  failures; the 5th halts with exit 78 so the service manager stops retrying (fix the key /
  IP whitelist, then start manually; a successful startup resets the count). Transient network errors exit with code 1.
  The auth-failure alert is sent at most once per hour per bot. `scripts/health.py` reports a
  `<bot>.softcut` marker as retired or soft-cut, not as a stale heartbeat.
- Optional `config/capital_events.yaml` records deposits and withdrawals so the dashboard does
  not count them as PnL (returns are time-weighted with these moves removed). It is gitignored
  and blocked by the pre-commit hook because it holds personal financial data. Format:

  ```yaml
  events:
    - {date: 2025-01-15, bot: my-bot, ledger: live, delta: 100}    # deposit
    - {date: 2025-02-01, bot: my-bot, ledger: live, delta: -50}    # withdrawal
  ```

  `ledger` is `live` (default) or `paper`; `date` is the UTC day the move shows up in equity.
  A missing or unparsable file means no events.
- The dashboard opens the SQLite ledgers read-only (`mode=ro`) and never creates or writes
  them; a missing ledger simply shows as empty. It binds to 127.0.0.1, rejects foreign `Host`
  headers and serves all page code from `monitor/static/` (no inline scripts).
- `storage/alerts.jsonl` (the dashboard's alert feed) is rotated to `alerts.jsonl.1` above
  ~5 MB. Telegram push is used only if `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set and
  well-formed.

## Project layout

```
core/        config loading, data models, polling cadence, network helpers
data/        exchange data fetcher, Parquet bar store, SQLite trade journal
strategies/  Strategy base class + example (sma_cross)
backtest/    event-driven engine and metrics
execution/   paper trader, spot/perp live executors, portfolio accounting, warmup
safety/      RiskGuard (non-bypassable risk checks)
monitor/     dashboard, heartbeats, alerts, logging, reports
sentiment/   optional sentiment sources, composite index, IC validation
scripts/     CLI entry points, systemd installer, git hooks
config/      system.yaml (global) and bots/*.yaml (one file per bot)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and [SECURITY.md](SECURITY.md)
for key handling.

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE) for attributions. Please also read the [DISCLAIMER](DISCLAIMER.md).
