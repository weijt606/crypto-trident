# Trident

> **暂缓公开发布（2026-10-20）。** 维护者已暂停开源发布，先完善安全与合规审查；现有许可证条款不变。

[![English](https://img.shields.io/badge/Language-English-lightgrey)](README.md)
[![简体中文](https://img.shields.io/badge/语言-简体中文-blue)](README.zh-CN.md)

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

Trident 是一个用 Python 编写的加密货币策略实验项目。你可以先用过去的价格测试交易规则
（回测），再用实时行情和虚拟资金观察它如何运行（模拟盘）。项目也包含由操作者自行控制的实验性
实盘执行器，会下达真实订单，需要单独审查使用资格、技术行为与运行条件。

内置示例根据最近 50 根和 200 根 K 线的平均价格变化生成信号，用来演示策略怎么写，盈利能力
未经验证。你可以换成自己的 Python 策略，在回测、模拟盘和实盘中复用。模拟成交与交易所实际
成交可能不同。

可以自己运行命令，也可以让自己的 Codex、Claude Code 或外部 API 编排的 Agent 辅助操作。
模型推理由你的 Agent 环境提供，回测和数值计算在运行 Python 的电脑或服务器上完成，详见
[使用自己的 Agent](#使用自己的-agent)。

公开行情通过 CCXT 接入。**当前实盘实现仅适配 Binance 现货和 USDT 本位永续合约。** 修改
`exchange:` 不代表新增了其他交易所的实盘支持；面板行情和交易所专属情绪数据目前也使用 Binance。
详见[通用交易所接入方案](docs/EXCHANGE_INTEGRATION.md)、[合规与数据流说明](docs/COMPLIANCE.md)
和[开源发布检查记录](docs/RELEASE_READINESS.md)。

> **免责声明。** Trident 是基础设施，不是交易策略。它只附带一个教科书式的示例（50/200 SMA
> 均线交叉）用于演示接口；该示例的盈利能力**未经验证**。交易加密资产存在重大亏损风险。本仓库中的
> 任何内容均不构成投资建议。详见完整[免责声明](DISCLAIMER.md)。
>
> - **不附带任何担保。** 本软件按"原样"提供，不附带任何形式的担保；见
>   [LICENSE](LICENSE)。操作者须审查和管理通过本软件下达的订单；法定权利和责任不受影响。
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

## 使用自己的 Agent

可以让外部 Agent 阅读代码、实现策略、运行测试和回测、分析脱敏后的报告，也可以在已授权的主机上
部署和检查模拟盘服务。维护者的个人工作流程使用 Agent 辅助操作；这个开源仓库提供相应的 CLI
入口、配置和本地记录。

- **Agent 指令：** [AGENTS.md](AGENTS.md) 记录开发约定与运维边界。需要你的工具读取或接收这份
  文件；它不是权限强制机制。
- **操作文档：** [Agent 辅助开发与运维](docs/AGENT_OPERATIONS.md) 列出任务对应的命令和副作用，
  说明 Linux 模拟盘服务部署方式，并提供可交给 Agent 的任务示例。
- **算力与 API：** 模型推理由外部 Agent 或模型 API 提供；数值计算和常驻执行使用运行 Python 的
  主机资源。交易所 API 提供数据及账户/订单访问，不提供模型推理算力。

个人工作流程还有专门的驱动模式，负责非交互 Agent 调用、模型选择、超时/重试和任务记录核验。
当前开源副本尚未移植该驱动器、任务队列或提供方配置，具体见[驱动器边界](docs/AGENT_OPERATIONS.md#driver-boundary--驱动器边界)。
当前仓库没有内置 LLM 客户端、自主 Agent 循环、模型交易信号适配器或远程任务/部署 API。示例 SMA
使用 K 线输入，新闻标题情绪使用词典，项目本身不要求模型 API key。实盘晋级和停机恢复仍由操作者
单独决定，不能在部署模拟盘时让 Agent 顺带修改。

## 面板

![面板：净值曲线与买入持有对比、bot 状态与成交记录](docs/images/dashboard-equity.png)

![面板：每日盈亏、风控限额与策略状态](docs/images/dashboard-strategy.png)

> 截图使用的是**完全合成的演示数据**：随机生成并标注为 BTC/USDT 的价格，以及内置 SMA 示例的
> 模拟交易。图中的净值、盈亏、胜率与买入持有对比仅用于界面演示，既不是回测结果、不是真实行情
> 历史，也不是真实或预期的交易结果，不代表示例策略的任何表现。

## 可以用它做什么

- **用历史数据测试想法。** 下载过去的行情，运行回测，查看收益、手续费和回撤（账户价值从高点
  下跌的幅度）。默认把历史数据按时间分成前 70% 和后 30%，方便分别检查表现。
- **先用虚拟资金试跑。** 模拟盘跟随当前公开行情，记录模拟买卖，支持现货与永续合约模拟。
  合约强平使用简化模型；模拟盘不需要交易 API key。
- **写自己的交易规则。** Python 策略读取一根已结束的 K 线和账户数据，返回买卖信号。
  交易对、K 线周期和策略参数放在配置文件里。
- **在面板里查看运行情况。** 本地网页展示账户价值、每日盈亏、持仓、成交和告警。
  数据和报告保存在本地，也可以选择把告警推送到 Telegram。
- **研究市场情绪。** 可选工具获取恐惧与贪婪指数、资金费率和多空比，辅助观察它们与后续价格
  变化的关系。这些是研究数据，不是已经验证有效的交易信号。
- **自己运行，或交给自己的 Agent 辅助。** 脚本可以下载数据、运行测试和回测、生成报告，
  Linux 服务可以让模拟盘持续运行。当前没有内置自主 Agent 驱动器，CI 也不会向你的机器部署代码。

实盘会检查密钥权限，以及配置的订单和亏损限额；这些检查不保证最大损失，也不保证一定能平仓。
接入账户前请阅读[实盘说明](docs/LIVE_TRADING.md)。接口和记账细节见[架构文档](docs/ARCHITECTURE.md)。

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

## 实盘实现范围

实验性实盘执行器在 Linux/macOS 上实现 Binance 现货和 USDT 本位永续下单路径。技术兼容不代表
某个交易所实体、账户或产品在你所在地区可用或获准使用。软件不核验法律资格、KYC 状态或适当性；
`stage: live` 仅记录操作者的配置选择，不代表监管批准。

- **自行部署运行。** 仓库提供软件和技术文档，不包含托管交易服务、资产托管、代管账户或个性化
  投资建议。凭据由操作者保管，不要为寻求支持而提交密钥。
- **执行结果可能不明。** 超时或查询失败不证明订单失败；停止 bot 不等于撤单或平仓。资金配置和
  本地风控阈值不隔离账户资产，也不保证亏损上限。
- **实盘须单独审查。** 回测、模拟盘、测试和密钥权限检查均不批准实盘运行。请阅读
  [实盘执行说明](docs/LIVE_TRADING.md)和[合规边界](docs/COMPLIANCE.md)，包括相关服务与推广的
  边界。源码可获取和 Apache 许可不等于取得金融业务许可。

首次使用先从上面的研究和模拟盘命令开始。实盘文档列有命令副作用、已知限制与人工审阅恢复说明。

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
