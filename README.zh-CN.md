# Trident

[English](README.md) | **简体中文**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Exchange connectivity: CCXT](https://img.shields.io/badge/exchange%20connectivity-CCXT-informational.svg)](docs/EXCHANGE_INTEGRATION.md)
[![Not investment advice](https://img.shields.io/badge/not-investment%20advice-red.svg)](DISCLAIMER.md)

**一套策略代码，三种环境：回测、模拟盘、实盘。**
仓库与包名：`crypto-trident`。

*为什么叫 Trident：一柄三尖，一份策略代码，跑通回测、模拟盘、实盘三种环境。*

> [!WARNING]
> **研究与个人项目，不构成任何投资建议。**
> **A research and personal project. Not investment advice.**
> 加密货币及衍生品交易风险极高，可能损失全部本金，使用风险自负。使用前请阅读完整[免责声明](DISCLAIMER.md)。
> Crypto and crypto-derivatives trading is highly risky and you can lose all of your capital.
> Use at your own risk. Read the full [DISCLAIMER](DISCLAIMER.md) before using this software.

Trident 是一个基于已收盘 OHLCV K 线开发和运行加密货币交易策略的 Python 框架。这个仓库用于
分享个人项目中的框架实现，并提供一份简单的示例配置，方便其他人阅读、复现和改造。内置的
50/200 SMA 均线交叉只演示策略接口，不代表策略推荐或盈利能力。

核心接口是 `Strategy.on_bar(bar, account)`：策略接收一根已收盘 K 线和账户快照，返回交易意图。
回测、模拟盘和实盘都使用这个接口，由各自的执行层处理数据、成交和记账。因此可以复用策略逻辑，
对比不同环境中的运行行为；这不代表模拟成交与交易所实际成交一致。

公开行情通过 CCXT 接入。**当前实盘实现仅适配 Binance 现货和 USDT 本位永续合约。** 修改
`exchange:` 不代表新增了其他交易所的实盘支持；面板行情和交易所专属情绪数据目前也使用 Binance。
详见[通用交易所接入方案](docs/EXCHANGE_INTEGRATION.md)、[合规与数据流说明](docs/COMPLIANCE.md)
和[开源发布检查记录](docs/RELEASE_READINESS.md)。

> **免责声明。** Trident 是基础设施，不是交易策略。它只附带一个教科书式的示例（50/200 SMA
> 均线交叉）用于演示接口；该示例的盈利能力**未经验证**。交易加密资产存在重大亏损风险。本仓库中的
> 任何内容均不构成投资建议。详见完整[免责声明](DISCLAIMER.md)。
>
> - **不附带任何担保。** 本软件按"原样"提供，不附带任何形式的担保；见
>   [LICENSE](LICENSE)。通过本软件下达的任何订单均由你本人自行负责。
> - **回测与模拟盘结果不代表实盘结果。** 实盘交易中的成交、延迟、手续费、资金费率与市场冲击
>   都与之不同。
> - **本项目与 Binance 或任何交易所均无关联，亦未获得其认可。** 交易所名称仅用于描述 API
>   兼容性。
> - **交易所和产品的可用性取决于地区与账户资格。** 接入账户前，须核对适用地区的法律和交易所条款。

## 三种运行环境有什么区别

| 环境 | 数据与成交方式 | 资金与输出 |
| --- | --- | --- |
| 回测 | 读取本地历史 K 线。策略在第 *t* 根收盘产生的订单，按第 *t+1* 根开盘价加配置的手续费、滑点模拟成交。 | 虚拟资金；输出净值曲线、成交、风险事件和 Markdown 指标报告。 |
| 模拟盘 | 轮询公开 REST 行情。按最新已收盘 K 线的收盘价加配置的手续费、滑点模拟成交。 | 虚拟资金；写入本地账本，可在面板查看；不需要交易 API key。 |
| 实盘 | 轮询已收盘 K 线，通过现有 Binance 执行器发送市价单，读取交易所余额、持仓和订单结果。 | 真实资金；需要凭据、权限检查，以及显式设置 `stage: live`。 |

通常先实现策略、下载历史数据、检查回测，再运行模拟盘并审阅账本，最后才考虑实盘。每个 bot 用
一份 YAML 配置策略、交易对、周期、市场类型和参数，全局配置定义费用与风险限额。每个策略实例
管理一个交易对；面板可以汇总多个 bot 进程，但没有在它们之间统一分配资金的组合管理器。

项目适合基于 K 线的策略实验、研究执行与记账实现，以及在这些模块上构建小型自托管工具。
行情和执行采用 REST 轮询，不是逐笔行情或订单簿交易引擎。公开数据覆盖取决于所选交易所的接口；
CCXT 中存在某家交易所，不代表本项目已经验证了对它的完整兼容。

## 面板

![面板：净值曲线与买入持有对比、bot 状态与成交记录](docs/images/dashboard-equity.png)

![面板：每日盈亏、风控限额与策略状态](docs/images/dashboard-strategy.png)

> 截图使用的是**完全合成的演示数据**：随机生成并标注为 BTC/USDT 的价格，以及内置 SMA 示例的
> 模拟交易。图中的净值、盈亏、胜率与买入持有对比仅用于界面演示，既不是回测结果、不是真实行情
> 历史，也不是真实或预期的交易结果，不代表示例策略的任何表现。

## 功能与实现特点

- **复用同一套策略接口。** 实现 `on_bar()` 输出信号，声明所需的预热长度；还可通过 `describe()`
  提供指标状态和触发条件，让面板显示策略当前在等待什么。在 `strategies.STRATEGIES` 注册后，
  通过 bot YAML 选择策略。启动时会拉取历史 K 线并重放以重建指标，策略也可关闭重放。
  策略不访问交易所是开发约定，并不是限制自定义代码能力的沙箱。
- **数据与实验记录保存在本地。** 通过 CCXT 下载公开 OHLCV 历史，使用 Parquet 存储 K 线，使用
  SQLite 记录成交、净值、事件和策略快照。命令行脚本生成 Markdown 报告，展示收益、回撤、
  Sharpe、手续费以及买入持有对比。回测命令默认按时间顺序划分 70% 样本内、30% 样本外数据，
  每段使用新的策略实例；`--oos` 可修改比例，`--oos 0` 会关闭样本外分段。
- **现货与永续合约模拟。** 现货记账跟踪现金、持仓成本和手续费；永续合约记账支持多空持仓、
  资金费结算和简化的强平模型。模拟盘可从本地账本恢复账户快照与持仓成本，但不会恢复实盘
  执行器那样的持久化熔断状态。回测和模拟盘复用记账实现，但成交参考价格不同，具体见上表。
- **信号与风控分开。** `RiskGuard` 检查交易对白名单、订单金额、持仓敞口、下单频率、日亏和回撤。
  日亏暂停会阻止增加敞口，回撤熔断会请求平仓并停机；紧急降低敞口的平仓路径是文档中明确记录的
  普通订单检查例外。随附的永续合约 CLI 路径将杠杆限制在 3× 以内。实盘保存熔断状态，已有 kill
  状态时拒绝继续运行，需要人工审阅和恢复。
- **实盘启动有明确检查。** 现货与永续执行器校验密钥权限、IP 白名单、交易对白名单和
  `stage: live`，并持有每个 bot 的 POSIX 文件锁。执行器处理交易所精度、订单限额和合约持仓模式，
  与交易所对账、持久化状态、使用客户端订单号，并在下单结果不确定时查询订单。这些措施用于减少
  部分故障风险，不保证每笔订单恰好执行一次，也不保证总能平仓。永续停役脚本会请求平仓、核验
  结果并停止 bot。
- **本地面板和运行观察。** 展示 bot 状态、净值、每日盈亏、买入持有对比、带成交标记的 K 线、
  成交记录、事件、告警、策略状态和配置的风控限额。面板只读访问账本，仅监听本机回环地址，
  静态资源随项目提供。心跳和健康检查用于观察轮询活动、停机标记及近期错误事件；可选 Telegram
  推送告警。
- **可选的情绪研究管线。** 获取恐惧与贪婪指数、资金费率和多空比；新闻 RSS、Reddit 需主动启用，
  Reddit 还需确认访问批准。数据按日标准化、加权和平滑，生成综合分数；可用时间戳和各源延迟
  决定框架在 K 线时间点提供哪些数值。独立 CLI 输出 Spearman IC 与分位收益，辅助比较动量、
  逆向假设；它不会自动批准或阻止实盘交易。
- **简单的运行与贡献工具。** 可以直接运行脚本，也可安装 Linux 用户级 systemd 服务和定时器。
  提交前钩子检查暂存内容中的密钥；轻量 CI 执行测试、密钥扫描和内置资源校验，手动运行时增加
  已知依赖漏洞检查。CI 不向个人运行环境部署代码。

## 当前范围与限制

- **只附带一个策略示例。** 仓库提供 SMA 均线交叉和 paper 阶段的演示配置，不包含一组已经验证
  有效的策略，也未发布盈利能力结论。
- **基于 K 线的成交假设。** 回测和模拟盘没有建模订单簿深度、排队位置或交易所延迟。永续合约
  强平使用简化参数，不能精确复现交易所的保证金档位和强平规则。手续费、滑点是可配置的假设，
  也可以设为零，模拟结果受这些设置影响。
- **本地风控有响应延迟。** 检查在新的已收盘 K 线上执行，并非持续检查；盘中波动、跳空和 API
  中断可能让损失超过配置阈值。项目不会自动设置交易所托管的保护性止损。
- **实盘覆盖和账户协调有限。** 当前实盘支持 Linux/macOS 上的 Binance 现货和 USDT 本位永续。
  多个进程之间没有自动资金分配，也不协调同一账户、同一交易对的管理权；应使用专用账户或
  子账户，避免多个执行器同时管理同一持仓。
- **研究检查仍需解释。** 时间分段或 IC 阈值不是统计显著性检验，也不证明未来表现。每次实验
  都需要审阅数据质量、源时间戳以及历史数据的可用性假设。

## 快速开始

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/weijt606/crypto-trident.git && cd crypto-trident
uv sync
git config core.hooksPath scripts/githooks      # 启用密钥扫描
uv run pytest                                    # 运行测试套件

# 1. 拉取历史数据（公开接口，无需 key）
uv run python scripts/fetch_data.py --symbol BTC/USDT --timeframe 1h --days 730

# 2. 回测示例 bot（config/bots/sma-demo.yaml）
uv run python scripts/run_backtest.py --bot sma-demo

# 3. 用实时行情跑模拟盘
uv run python scripts/run_paper.py --bot sma-demo

# 4. 查看运行情况
uv run python -m monitor.dashboard               # http://127.0.0.1:8777（用 --port 修改端口）
uv run python scripts/report.py --bot sma-demo
```

示例 bot 在 1h K 线上需要 200 根预热，因此回测所需的历史数据要远多于 200 根；730 天绰绰有余。

可选扩展：

```bash
# 情绪管线（见下文"数据源与条款"）
uv run python scripts/fetch_sentiment.py --backfill
uv run python scripts/fetch_data.py --symbol BTC/USDT --timeframe 1d --days 2000   # 验证需要 1d K 线
uv run python scripts/validate_sentiment.py --symbol BTC/USDT
```

永续合约回测（`market: perp`）需要资金费率历史文件：BTC 用
`storage/sentiment/funding.parquet`（由 `fetch_sentiment.py` 写入），其他币种用
`storage/sentiment/funding_<BASE>.parquet`。

## 编写策略

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
        return 50                      # 产生第一个信号前所需的 K 线数

    def on_bar(self, bar: Bar, account: AccountView) -> list[Signal]:
        # 使用 K 线和账户输入，指标可保存在策略状态中。
        # 执行层负责联网和下单，策略不判断运行环境。
        if account.position_qty == 0 and bar.close > bar.open:
            return [Signal(Side.BUY, account.equity * self.params["alloc"] / bar.close, reason="up bar")]
        return []
```

在 `strategies/__init__.py`（`STRATEGIES`）中注册它，然后把 `config/bots/sma-demo.yaml`
复制为新文件，设置 `strategy:` 和 `params:`，再用 `--bot <file name>` 运行。

## 上实盘（请先读这一节）

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
使用实盘适配器前，请阅读[合规边界](docs/COMPLIANCE.md)。

## 情绪配置

所有键都是可选的；如需修改，在 `config/system.yaml` 中添加 `sentiment:` 块。

```yaml
sentiment:
  sources: [fear_greed, funding, long_short]   # 默认值；加入 news 和/或 reddit 以开启
  weights: {}                                   # 空 = 已启用的数据源等权
  z_window: 60                                  # 滚动 z-score 窗口（天）
  ema_span: 5                                   # 平滑（天）
  regime_cutoffs: [-60, 60]                     # 极度恐惧 / 极度贪婪标签阈值
```

## 数据源与条款

每个数据源都有各自的条款，遵守这些条款是你自己的责任。

- **恐惧与贪婪指数**（Fear & Greed Index），来自 [alternative.me](https://alternative.me/crypto/fear-and-greed-index/)：
  注明出处即可免费使用：在任何展示该数据的位置旁边注明 alternative.me（面板已这样做），且不要把它
  当作你自己的服务来呈现。
- **交易所行情数据**（K 线、行情、资金费率、多空比）：公开接口，受交易所 API 条款与频率限制约束。
- **Reddit**（需主动开启）：使用 Reddit 公开的免鉴权 RSS/JSON 接口。Reddit 的用户协议
  （User Agreement）、数据 API 条款（Data API Terms）和负责任开发者政策（Responsible Builder
  Policy）均适用。启用前须完成其要求的注册和访问许可；低频请求不等于获得授权。确认后才可设置
  `TRIDENT_REDDIT_ACCESS_APPROVED=true`。此设置只是操作者的确认，不是本项目授予的数据访问权限。
  请在 `.env` 中把 `TRIDENT_USER_AGENT` 设为带联系方式的描述性值，例如
  `trident/0.1 (by u/yourname; you@example.com)`，并保持较低的请求频率（最多每小时一次）。
  未设置授权确认或描述性 User-Agent 时，不会发起 Reddit 请求。
- **新闻 RSS 源**（需主动开启）：使用前核对各提供方条款。未取得相应权利时，不要再分发存储的标题
  （`storage/sentiment/news.parquet`）。
- **api.ipify.org**：仅在交易所鉴权失败后调用，用于报告你的出口 IP，方便你把它加入白名单。
- **Telegram**（可选）：配置后会向 Telegram 及目标会话发送告警文本，可能包括 bot 名、交易对、
  数量、价格、资金规模和风险事件。两个 Telegram 设置均留空时告警只保存在本地。分享日志前应审阅。

## 配置说明

- `scripts/fetch_data.py --exchange` 独立选择历史数据源，不会自动读取 `config/system.yaml`；
  回测时要保持数据源一致。模拟盘轮询读取 `system.exchange`，但启动预热目前默认从 Binance
  拉取；单改这个设置并不等于完成其他交易所的接入。
- `config/system.yaml` 中的默认值刻意设得很小：回测/模拟盘 `initial_cash: 1000`，
  `risk.max_order_notional: 1000`，实盘 `cash_cap: 100`。请按你自己的资金设置。
- 环境变量覆盖：`TRIDENT_STORAGE_DIR`、`TRIDENT_LOG_DIR`、`TRIDENT_USER_AGENT`。
  每一项都可以在进程环境或 `.env` 中设置（进程环境优先）。
  `.env` 中的值按需读取，**不会**导出到进程环境中；已注入进程环境的变量仍按普通规则被子进程继承。
  若覆盖 storage/log 目录，须自行确认新位置也已从 git 排除。
- 执行器被风控停机时（熔断、soft cut、启动安全检查失败、`stage` 未设为 `live`）以退出码 **78**
  退出。systemd 单元设置了 `RestartPreventExitStatus=78`，因此被停机的 bot 会保持停止，直到有人
  排查。启动时鉴权失败以退出码 1 退出（可重启），直到连续 5 次失败；第 5 次以退出码 78 停机，让
  服务管理器停止重试（修好 key / IP 白名单后再手动启动；一次成功启动会重置计数）。临时网络错误以
  退出码 1 退出。鉴权失败告警每个 bot 每小时最多发送一次。`scripts/health.py` 会把
  `<bot>.softcut` 标记报告为已停役或 soft cut，而不是心跳过期。
- 可选的 `config/capital_events.yaml` 用于记录入金和出金，面板按已填写的记录调整收益和每日盈亏。
  未记录的资金变动不会自动识别；独立 CLI 指标报告不应用这项调整。该文件含个人财务数据，
  已被 gitignore 且被 pre-commit 钩子拦截。格式：

  ```yaml
  events:
    - {date: 2025-01-15, bot: my-bot, ledger: live, delta: 100}    # 入金
    - {date: 2025-02-01, bot: my-bot, ledger: live, delta: -50}    # 出金
  ```

  `ledger` 为 `live`（默认）或 `paper`；`date` 是该笔资金变动体现在净值中的 UTC 日期。
  文件缺失或无法解析时视为没有事件。
- 面板以只读方式（`mode=ro`）打开 SQLite 账本，从不创建或写入账本；账本缺失时只会显示为空。面板
  绑定 127.0.0.1，拒绝外部 `Host` 请求头，所有页面代码都从 `monitor/static/` 提供（无内联脚本）。
- `storage/alerts.jsonl`（面板的告警流）超过约 5 MB 时轮转为 `alerts.jsonl.1`。只有在
  `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 均已设置且格式正确时，才会使用 Telegram 推送。

## 项目结构

```
core/        配置加载、数据模型、轮询节奏、网络工具
data/        交易所数据拉取器、Parquet K 线存储、SQLite 交易账本
strategies/  Strategy 基类 + 示例（sma_cross）
backtest/    事件驱动引擎与指标
execution/   模拟盘、现货/永续合约实盘执行器、组合记账、预热
safety/      共享风控检查、敞口限额、暂停与熔断状态
monitor/     面板、心跳、告警、日志、报告
sentiment/   可选情绪数据源、综合指数、IC 验证
scripts/     CLI 入口、systemd 安装脚本、git 钩子
config/      system.yaml（全局）与 bots/*.yaml（每个 bot 一个文件）
```

设计详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，密钥处理详见 [SECURITY.md](SECURITY.md)。

## 许可证

基于 [Apache License, Version 2.0](LICENSE) 授权。署名信息见 [NOTICE](NOTICE)。另请阅读[免责声明](DISCLAIMER.md)。

> 本文为英文 README 的中文翻译；如有出入，以英文版为准。
