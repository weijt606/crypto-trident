# Trident

> **Publication on hold (2026-10-20).** The maintainer has paused public release pending security and compliance review. Existing license terms are unchanged.

[![English](https://img.shields.io/badge/Language-English-blue)](README.md)
[![简体中文](https://img.shields.io/badge/语言-简体中文-lightgrey)](README.zh-CN.md)

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Exchange connectivity: CCXT](https://img.shields.io/badge/exchange%20connectivity-CCXT-informational.svg)](docs/EXCHANGE_INTEGRATION.md)
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

Trident is a Python project for testing crypto trading ideas. You can try a rule on past
prices (a backtest), then watch it run on current market data with virtual money (paper
trading). It also includes experimental live executors for operator-controlled use. They
place real orders and require separate eligibility, technical and operational review.

The example uses two moving averages of price, over 50 and 200 bars, to demonstrate how a
strategy works. Its profitability has not been established. You can replace it with your
own Python strategy and reuse that code across backtest, paper and live modes. Simulated
trades can differ from real exchange fills.

You can run the commands yourself or ask your own Codex, Claude Code or external API-based
agent to help. Model reasoning comes from your agent setup; backtests and calculations run
on the machine running Python. See [Using your own agent](#using-your-own-agent).

Public market data uses CCXT. The current live implementation is specific to **Binance spot
and USDT-margined perpetuals**. Changing `exchange:` does not add live support for another
venue; dashboard tickers and exchange-specific sentiment feeds also currently use Binance.
See the [exchange integration design](docs/EXCHANGE_INTEGRATION.md),
[compliance and data flows](docs/COMPLIANCE.md), and
[release readiness record](docs/RELEASE_READINESS.md).

> **Disclaimer.** Trident is infrastructure, not a trading strategy. It ships one textbook
> example (a 50/200 SMA crossover) to demonstrate the API; its profitability has **not** been
> established. Trading crypto assets carries substantial risk of loss. Nothing in this
> repository is financial advice. See the full [DISCLAIMER](DISCLAIMER.md).
>
> - **No warranty.** The software is provided "as is", without warranty of any kind; see
>   [LICENSE](LICENSE). Operators must review and manage orders placed with it; mandatory
>   legal rights and liabilities are unaffected.
> - **Backtest and paper results do not predict live results.** Fills, latency, fees,
>   funding and market impact differ in live trading.
> - **Not affiliated with or endorsed by Binance or any exchange.** Exchange names are used
>   only to describe API compatibility.
> - **Exchange and product access depend on location and account eligibility.** Check the
>   laws of your jurisdiction and the applicable exchange terms before connecting an account.

## How the three environments differ

| Environment | Data and execution | Funds and outputs |
| --- | --- | --- |
| Backtest | Reads stored historical bars. Strategy orders generated at bar *t* close are simulated at bar *t+1* open, with configured fees and slippage. | Virtual funds; equity curve, fills, risk events and Markdown metrics report. |
| Paper | Polls public REST market data. Signals are simulated at the latest closed bar's close, with configured fees and slippage. | Virtual funds; local ledger and dashboard. No trading API key is needed. |
| Live | Polls closed bars and sends market orders through the implemented Binance executors. Reads exchange balances, positions and order results. | Real funds; requires credentials, permission checks and an explicit `stage: live` setting. |

A typical workflow is to implement a strategy, fetch history, inspect a backtest, run paper
trading and review its ledger before considering live operation. Each bot has a YAML file for
its strategy, symbol, timeframe, market and parameters; global settings define costs and risk
limits. Each strategy instance manages one symbol. Several bot processes can be monitored
in one dashboard, but there is no shared portfolio allocator across them.

The project is suited to bar-based strategy experiments, studying execution and accounting
code, and building small, self-hosted tools around those pieces. It uses REST polling rather
than a tick or order-book execution engine. Public-data coverage depends on the selected
exchange's endpoints; CCXT support alone does not establish compatibility with this project.

## Using your own agent

You can ask an external agent to read the code, implement a strategy, run tests and
backtests, inspect masked reports, or deploy and check paper services on an authorized
host. The maintainer uses agents in their personal workflow; the open-source repository
provides CLI entry points, configuration and local records for that workflow.

- **Agent instructions:** [AGENTS.md](AGENTS.md) records development conventions and
  operational boundaries. Your tool must read or be given the file; it is not a permission
  enforcement mechanism.
- **Operations guide:** [Agent-assisted development and operations](docs/AGENT_OPERATIONS.md)
  maps tasks to existing commands, explains their side effects, and describes Linux paper
  service deployment and example agent requests.
- **Compute and APIs:** model reasoning comes from your external agent/provider setup.
  Numerical computation and long-running execution use the machine running Python. Exchange
  APIs supply data and account/order access; they are not model-compute services.

The personal workflow also has a dedicated driver pattern for non-interactive agent calls,
model selection, timeout/retry handling and checking task records. The current open-source
copy has not ported that driver, task queue or provider configuration; the
[driver boundary](docs/AGENT_OPERATIONS.md#driver-boundary--驱动器边界) explains the distinction.
There is no bundled LLM client, autonomous agent loop, model-generated trading signal adapter
or remote job/deployment API. The included SMA uses bar inputs; optional headline sentiment
uses a lexicon. No model API key is required by this repository. Live promotion and recovery
remain separate operator decisions; agents should not change them as part of paper setup.

## Dashboard

![Dashboard: equity curve vs buy & hold, bot status and fills](docs/images/dashboard-equity.png)

![Dashboard: daily PnL, risk limits and strategy state](docs/images/dashboard-strategy.png)

> Screenshots use **fully synthetic demo data**: randomly generated prices labelled BTC/USDT and
> simulated trades of the bundled SMA example. The equity, PnL, win-rate and buy-and-hold figures
> are illustrative only. They are not a backtest, not real market history and not real or expected
> trading results, and say nothing about how the example strategy would perform.

## What you can do

- **Test an idea on past prices.** Download historical data and run a backtest. The report
  shows returns, fees and drawdown (the fall from a previous account-value peak). By default,
  it splits the history into an earlier 70% and a later 30% so you can inspect each separately.
- **Try it with virtual money.** Paper trading follows current public market data and records
  simulated trades. It supports spot and perpetual futures simulations, with a simplified
  liquidation model. No trading API key is needed.
- **Write your own rules.** A Python strategy reads a completed price bar and account values,
  then returns buy or sell signals. A configuration file selects the trading pair, bar
  interval and strategy settings.
- **See what happened.** The local dashboard shows account value, daily profit and loss,
  positions, trades and alerts. Data and reports are stored locally. Telegram alerts are optional.
- **Explore market sentiment.** Optional tools collect the Fear & Greed index, funding rates
  and long/short ratios for comparison with later price moves. These are research inputs,
  not proven trading signals.
- **Use scripts or your own agent.** Commands cover data downloads, tests, backtests and
  reports. Linux services can keep paper trading running. There is no built-in autonomous
  agent driver, and CI does not deploy to your machine.

Live execution checks key permissions and configured order and loss limits. These checks do
not guarantee a maximum loss or successful closing of a position. Read the
[live execution guide](docs/LIVE_TRADING.md) before connecting an account. Interface and
accounting details are in the [architecture guide](docs/ARCHITECTURE.md).

## Current scope and limits

- **One bundled example.** The repository includes the SMA crossover and a paper-stage demo
  configuration, not a catalog of validated strategies or published profitability results.
- **Bar-level execution assumptions.** Backtests and paper trading do not model order-book
  depth, queue position or exchange latency. Perpetual liquidation uses simplified parameters,
  not an exact reproduction of exchange margin tiers and liquidation rules. Fees and slippage
  are configurable assumptions and can be set to zero; simulated returns depend on them.
- **Delayed local risk response.** Risk checks run on new closed bars, not continuously.
  Intrabar moves, gaps and API outages can exceed configured loss thresholds. No
  exchange-hosted protective stop is installed automatically.
- **Limited live coverage and account coordination.** Current live support is Binance spot
  and USDT-M perpetuals on Linux/macOS. There is no automatic capital allocation or
  coordination between bot processes sharing an account and symbol; use dedicated accounts
  or sub-accounts and avoid competing managers of the same position.
- **Research checks require interpretation.** A chronological split or an IC threshold is
  not a statistical significance test or proof of future performance. Source timestamps,
  historical data quality and availability assumptions need review for each experiment.

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
        # Use bar/account inputs; keep any indicators in strategy state.
        # Leave network access and order placement to the execution layer.
        if account.position_qty == 0 and bar.close > bar.open:
            return [Signal(Side.BUY, account.equity * self.params["alloc"] / bar.close, reason="up bar")]
        return []
```

Register it in `strategies/__init__.py` (`STRATEGIES`), then copy `config/bots/sma-demo.yaml`
to a new file, set `strategy:` and `params:`, and run it with `--bot <file name>`.

## Live execution scope

The experimental live executors implement Binance spot and USDT-M perpetual order paths
on Linux/macOS. Technical compatibility does not establish that a particular exchange
entity, account or product is available or permitted in your location. The software does
not verify legal eligibility, KYC status or suitability; `stage: live` records an operator's
configuration choice, not regulatory approval.

- **Self-hosted operation.** This repository supplies software and technical documentation.
  It does not include a hosted trading service, custody, managed accounts or personalized
  investment advice. Keep credentials with the operator; do not submit them for support.
- **Execution can remain unresolved.** A timeout or failed lookup does not establish that
  an order failed. Stopping a bot does not cancel exchange orders or close positions.
  Capital caps and local risk thresholds do not isolate account assets or guarantee a loss ceiling.
- **Separate review before live use.** Backtests, paper results, tests and key-permission
  checks do not approve live operation. Review the [live execution notes](docs/LIVE_TRADING.md)
  and [compliance scope](docs/COMPLIANCE.md), including the boundaries of any related service
  or promotion. Source availability and Apache licensing do not confer a financial license.

Start with the research and paper commands above. See the live guide for command side
effects, known limitations and human-reviewed recovery.

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
  disallow unauthenticated automated access. Obtain any required registration and permission
  before enabling this source; low request rates do not establish authorization. Set
  `TRIDENT_REDDIT_ACCESS_APPROVED=true` only after that review. This is your acknowledgement,
  not an authorization granted by this project.
  Set `TRIDENT_USER_AGENT` in `.env` to a descriptive value with contact info, e.g.
  `trident/0.1 (by u/yourname; you@example.com)`, and keep request rates low (hourly at most).
  Without the acknowledgement and a descriptive User-Agent, the fetcher makes no requests.
- **News RSS feeds** (opt-in): review each provider's terms before use. Do not redistribute
  stored headlines (`storage/sentiment/news.parquet`) without the necessary rights.
- **api.ipify.org**: called only after an exchange authentication failure, to report your
  outbound IP so you can whitelist it.
- **Telegram** (optional): when configured, sends alert text to Telegram and the selected
  chat. Alerts may include bot names, symbols, quantities, prices, capital and risk events.
  Leave both Telegram settings empty to keep alerts local. Review logs before sharing them.

## Configuration notes

- `scripts/fetch_data.py --exchange` selects the historical-data source independently of
  `config/system.yaml`. Keep it aligned with the source used by a backtest. The paper polling
  client reads `system.exchange`, but startup warmup currently defaults to Binance; changing
  that setting alone is not a complete migration to another exchange.
- Defaults in `config/system.yaml` are deliberately small: backtest/paper `initial_cash: 1000`,
  `risk.max_order_notional: 1000`, live `cash_cap: 100`. Set them to match your own capital.
- Environment overrides: `TRIDENT_STORAGE_DIR`, `TRIDENT_LOG_DIR`, `TRIDENT_USER_AGENT`.
  Each may be set in the process environment or in `.env` (the process environment wins).
  `.env` values are read on demand and are **not** exported into the process environment.
  Values already injected into the environment retain normal child-process inheritance.
  If overriding storage/log directories, ensure the new locations are also excluded from git.
- Executors exit with code **78** when halted by the risk guard (kill switch, soft cut, failed
  startup safety check, `stage` not set to `live`). The systemd units set
  `RestartPreventExitStatus=78`, so a halted bot stays down until a human investigates.
  Startup authentication failures exit with code 1 (restartable) until 5 consecutive
  failures; the 5th halts with exit 78 so the service manager stops retrying (fix the key /
  IP whitelist, then start manually; a successful startup resets the count). Transient network errors exit with code 1.
  The auth-failure alert is sent at most once per hour per bot. `scripts/health.py` reports a
  `<bot>.softcut` marker as retired or soft-cut, not as a stale heartbeat.
- Optional `config/capital_events.yaml` records deposits and withdrawals. The dashboard
  adjusts the equity curve, returns, PnL and daily-PnL figures for those supplied entries
  (an entry dated on a day without a snapshot is applied at the next one); it does not
  know the intraday timing of a move. Events are assumed to occur at the first snapshot
  on or after their UTC date; events through the starting snapshot's date are included
  in opening capital. Returns and drawdown use recorded snapshots, so the adjustment
  is an approximation when capital moves between snapshots. The dashboard does not
  detect unrecorded moves automatically. The CLI metrics report does not apply this
  adjustment. The file is gitignored and blocked by the pre-commit hook because it holds
  personal financial data. Format:

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
safety/      shared risk checks, exposure limits, halt and kill state
monitor/     dashboard, heartbeats, alerts, logging, reports
sentiment/   optional sentiment sources, composite index, IC validation
scripts/     CLI entry points, systemd installer, git hooks
config/      system.yaml (global) and bots/*.yaml (one file per bot)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design,
[docs/AGENT_OPERATIONS.md](docs/AGENT_OPERATIONS.md) for agent-assisted operation, and
[SECURITY.md](SECURITY.md) for key handling.

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE) for attributions. Please also read the [DISCLAIMER](DISCLAIMER.md).
