# Live execution notes / 实盘执行说明

These notes describe the current implementation; they are not an exchange recommendation,
a legal authorization or a guarantee of execution. Read [DISCLAIMER](../DISCLAIMER.md),
[COMPLIANCE](COMPLIANCE.md) and [SECURITY](../SECURITY.md) first. Publishing technical setup
instructions does not establish that live use is permitted for a particular operator.

本文记录当前实现的配置方式，不构成交易所推荐、法律许可或执行保证。先阅读上述免责声明、
合规边界和安全说明。将说明移至此处不会改变代码功能或消除相关风险。
Commands below are run from the repository root. / 下列命令均在仓库根目录执行。

## English

1. Paper trade long enough to trust the behavior, review an out-of-sample backtest, and run the
   circuit-breaker drill (`uv run pytest tests/test_live_drill.py`). This is a minimum engineering
   check, not evidence of future profitability.
2. Create a **dedicated** Binance API key: reading + trading only, **withdrawals disabled**,
   **IP whitelist** on, ideally in a sub-account holding only capital you can afford to lose.
3. Put trading keys in `.env` (copy `.env.example`, `chmod 600 .env`) or an externally managed
   process environment. Public data and dashboard commands need no key. `BINANCE_READ_*`
   settings are reserved for private read-only integrations, not used by the bundled scripts.
4. `uv run python scripts/verify_trade_key.py` to check permissions.
5. Set `live.cash_cap` / `initial_cash` in `config/system.yaml` (or `cash_cap` in the bot yaml for
   perps), review the `risk:` limits (including `allowed_symbols`, which live executors
   require), then **manually** change the bot's `stage` to `live`.
6. `uv run python scripts/run_live.py --bot <name>` (spot) or `uv run python scripts/run_live_perp.py --bot <name>` (perp).
7. If the kill switch fires, investigate, then `uv run python scripts/revive.py --bot <name> --reviewed "Reviewed halt cause, account positions and risk limits"`.

Run only **one** live executor per bot at any time, on **Linux or macOS**; live execution
refuses to start without POSIX file locking. Use a dedicated account/sub-account and do not
run several bots against the same account and symbol. Missing or malformed key-permission
fields, a missing IP whitelist, or a group/world-accessible `.env` refuse live startup.
Run manually with `umask 077` to keep newly created runtime data private.

**Risk checks run on new closed bars, not continuously.** A configured 15% drawdown threshold
is not a maximum-loss guarantee. Intrabar moves, gaps, stale data, API outages and inability
to close a position can cause larger losses. No exchange-hosted protective stop is installed.
Read [COMPLIANCE.md](COMPLIANCE.md) before using the live adapter.

## 中文

1. 模拟盘跑足够长的时间，直到你信任它的行为；审阅样本外回测；并运行熔断演练
   （`uv run pytest tests/test_live_drill.py`）。这只是最低限度的工程检查，不是未来盈利的证据。
2. 创建一个**专用**的 Binance API key：只开读取 + 交易权限，**禁用提现**，开启 **IP 白名单**，
   最好放在一个只存放你能承受损失的资金的子账户中。
3. 把交易 key 放进 `.env`（复制 `.env.example`，`chmod 600 .env`）或由外部管理的进程环境中。
   公开数据与面板不需要 key；`BINANCE_READ_*` 是给私有只读接入预留的配置，随附脚本目前不使用。
4. 运行 `uv run python scripts/verify_trade_key.py` 检查权限。
5. 在 `config/system.yaml` 中设置 `live.cash_cap` / `initial_cash`（永续合约则在 bot yaml 中设置
   `cash_cap`），审阅 `risk:` 限额（包括实盘执行器必需的 `allowed_symbols`），然后**手动**把 bot
   的 `stage` 改为 `live`。
6. 运行 `uv run python scripts/run_live.py --bot <name>`（现货）或 `uv run python scripts/run_live_perp.py --bot <name>`（永续合约）。
7. 如果触发熔断，先排查原因，再运行 `uv run python scripts/revive.py --bot <name> --reviewed "已检查停机原因、账户持仓与风险限额，确认恢复"`。

任何时刻每个 bot 只能运行**一个**实盘执行器，实盘仅支持 **Linux / macOS**；缺少 POSIX 文件锁
时拒绝运行。使用专用账户或子账户，不要让多个 bot 同时管理同一账户中的同一交易对。权限字段
缺失或异常、未绑定 IP 白名单，以及 `.env` 可被组内或其他用户访问，都会导致实盘拒绝启动。
手动运行前执行 `umask 077`，保护新建的本地运行数据。

**风控在新的已收盘 K 线上检查，并非持续检查。** 15% 回撤阈值不等于最大损失保证；盘中波动、
跳空、行情过期、API 中断和平仓失败都可能使损失扩大。项目没有自动设置交易所托管的保护性止损。
使用实盘适配器前，请阅读[合规边界](COMPLIANCE.md)。
