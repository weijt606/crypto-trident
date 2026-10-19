# Trident

[English](README.md) | **简体中文**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
[![Exchange: Binance via ccxt](https://img.shields.io/badge/exchange-Binance%20%28ccxt%29-informational.svg)](https://github.com/ccxt/ccxt)
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

Trident 是一个小而有明确取舍的 Python 加密货币量化交易框架。策略就是一个普通的类：读入已收盘的
K 线，输出信号。同一个类无需任何改动，即可运行在事件驱动的回测器中、由实时行情驱动的模拟盘中，
以及 Binance 现货和 USDT 本位永续合约的实盘执行器中。在任何环境下，每一笔订单都要经过同一个
独立的风控。

> **免责声明。** Trident 是基础设施，不是交易策略。它只附带一个教科书式的示例（50/200 SMA
> 均线交叉）用于演示接口；该示例**不**应被期望盈利。交易加密资产存在重大亏损风险。本仓库中的
> 任何内容均不构成投资建议。详见完整[免责声明](DISCLAIMER.md)。
>
> - **不附带任何担保。** 本软件按"原样"提供，不附带任何形式的担保；见
>   [LICENSE](LICENSE)。通过本软件下达的任何订单均由你本人自行负责。
> - **回测与模拟盘结果不代表实盘结果。** 实盘交易中的成交、延迟、手续费、资金费率与市场冲击
>   都与之不同。
> - **本项目与 Binance 或任何交易所均无关联，亦未获得其认可。** 交易所名称仅用于描述 API
>   兼容性。
> - **交易所的可用性受地域限制**（例如 Binance 不向美国人士提供服务）。你须遵守你所在司法辖区的
>   法律以及所用交易所的服务条款。

## 面板

![面板：净值曲线与买入持有对比、bot 状态与成交记录](docs/images/dashboard-equity.png)

![面板：每日盈亏、风控限额与策略状态](docs/images/dashboard-strategy.png)

> 截图使用的是**完全合成的演示数据**：随机生成并标注为 BTC/USDT 的价格，以及内置 SMA 示例的
> 模拟交易。图中的净值、盈亏、胜率与买入持有对比仅用于界面演示，既不是回测结果、不是真实行情
> 历史，也不是真实或预期的交易结果，不代表示例策略的任何表现。

## 功能

- **交易所接口**，基于 [ccxt](https://github.com/ccxt/ccxt)（Binance 现货 + USDT 本位永续合约）：
  公开行情数据、只读账户访问、按交易所精度与最小名义金额处理的下单、支持双向持仓模式的永续合约
  下单、遇时间戳错误时重新校时、鉴权失败检测并报告出口 IP（告警中打码，完整 IP 只写入本地日志；
  通过 `api.ipify.org` 查询，且仅在鉴权失败时查询）。
- **数据层**：OHLCV 拉取器、Parquet K 线存储、SQLite 交易账本（成交、事件、净值）。
- **回测器**：事件驱动，信号在第 *t* 根 K 线收盘产生、第 *t+1* 根开盘成交，始终计入手续费与
  滑点，每份报告都分样本内 / 样本外两段，并模拟永续合约保证金与强平。
- **模拟盘**：轮询实时 K 线，用与回测器相同的成本模型模拟成交，状态可恢复，启动预热会重建指标
  状态而不下单。
- **实盘执行器**：现货与永续合约，受 stage 门控（必须由人手动设置 `stage: live`），未配置
  `risk.allowed_symbols` 时拒绝启动；与交易所对账、每次成交即落盘、熔断 kill 文件、永续合约受控停役
  脚本（`scripts/retire_live_perp.py`，平仓并停机）。
- **风控**（`safety/guard.py`）：交易对白名单、单笔最大名义金额、最大持仓、下单频率限制、
  日亏暂停（HALT）、最大回撤熔断（需人工恢复）。策略无法关闭它。无论配置如何，永续合约杠杆都被
  风控覆盖层和执行器硬性限制在 3× 以内。
- **监控**：本地网页面板（bot、净值曲线、带成交标记的 K 线、成交、事件、每日盈亏、行情、健康
  状态；仅监听本机回环地址、只读访问账本、严格 CSP、不加载第三方资源）、心跳、健康检查、可选
  Telegram 告警。
- **情绪管线（可选）**：默认包括恐惧与贪婪指数以及交易所资金费率和多空比；新闻 RSS 与 Reddit
  需主动开启。可用时间戳防止前视，IC + 分位验证门槛用于检验动量与逆向假设。
- **运维**：用户级 systemd 安装脚本、pre-commit 密钥扫描。

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
        # 只依赖过去 K 线 + 只读账户视图的纯函数：
        # 不联网、不读时钟、不判断运行环境
        if account.position_qty == 0 and bar.close > bar.open:
            return [Signal(Side.BUY, account.equity * self.params["alloc"] / bar.close, reason="up bar")]
        return []
```

在 `strategies/__init__.py`（`STRATEGIES`）中注册它，然后把 `config/bots/sma-demo.yaml`
复制为新文件，设置 `strategy:` 和 `params:`，再用 `--bot <file name>` 运行。

## 上实盘（请先读这一节）

1. 模拟盘跑足够长的时间，直到你信任它的行为，并审阅样本外回测。这只是最低限度的工程检查，
   不是未来盈利的证据。
2. 创建一个**专用**的 Binance API key：只开读取 + 交易权限，**禁用提现**，开启 **IP 白名单**，
   最好放在一个只存放你能承受损失的资金的子账户中。
3. 把 key 放进 `.env`（复制 `.env.example`，`chmod 600 .env`）。数据/监控使用单独的只读 key；
   只有实盘执行器和 `verify_trade_key.py` 会读取交易 key。
4. 运行 `uv run python scripts/verify_trade_key.py` 检查权限。
5. 在 `config/system.yaml` 中设置 `live.cash_cap` / `initial_cash`（永续合约则在 bot yaml 中设置
   `cash_cap`），审阅 `risk:` 限额（包括实盘执行器必需的 `allowed_symbols`），然后**手动**把 bot
   的 `stage` 改为 `live`。
6. 运行 `uv run python scripts/run_live.py --bot <name>`（现货）或 `uv run python scripts/run_live_perp.py --bot <name>`（永续合约）。
7. 如果触发熔断，先排查原因，再运行 `uv run python scripts/revive.py --bot <name> --reviewed "..."`。

任何时刻每个 bot 只能运行**一个**实盘执行器。

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
  Policy）均适用，Reddit 可能屏蔽或不允许免鉴权的自动化访问；是否启用该数据源由你自行决定。
  请在 `.env` 中把 `TRIDENT_USER_AGENT` 设为带联系方式的描述性值，例如
  `trident/0.1 (by u/yourname; you@example.com)`，并保持较低的请求频率（最多每小时一次）。
- **新闻 RSS 源**（需主动开启）：仅供个人使用。不要再分发已存储的标题
  （`storage/sentiment/news.parquet`）。
- **api.ipify.org**：仅在交易所鉴权失败后调用，用于报告你的出口 IP，方便你把它加入白名单。

## 配置说明

- `config/system.yaml` 中的默认值刻意设得很小：回测/模拟盘 `initial_cash: 1000`，
  `risk.max_order_notional: 1000`，实盘 `cash_cap: 100`。请按你自己的资金设置。
- 环境变量覆盖：`TRIDENT_STORAGE_DIR`、`TRIDENT_LOG_DIR`、`TRIDENT_USER_AGENT`。
  每一项都可以在进程环境或 `.env` 中设置（进程环境优先）。
  key 按需从 `.env` 读取，**不会**导出到进程环境中。
- 执行器被风控停机时（熔断、soft cut、启动安全检查失败、`stage` 未设为 `live`）以退出码 **78**
  退出。systemd 单元设置了 `RestartPreventExitStatus=78`，因此被停机的 bot 会保持停止，直到有人
  排查。启动时鉴权失败以退出码 1 退出（可重启），直到连续 5 次失败；第 5 次以退出码 78 停机，让
  服务管理器停止重试（修好 key / IP 白名单后再手动启动；一次成功启动会重置计数）。临时网络错误以
  退出码 1 退出。鉴权失败告警每个 bot 每小时最多发送一次。`scripts/health.py` 会把
  `<bot>.softcut` 标记报告为已停役或 soft cut，而不是心跳过期。
- 可选的 `config/capital_events.yaml` 用于记录入金和出金，使面板不把它们计为盈亏（收益按剔除这些
  资金变动后的时间加权计算）。该文件含个人财务数据，因此已被 gitignore 且被 pre-commit 钩子拦截。
  格式：

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
safety/      RiskGuard（不可绕过的风控检查）
monitor/     面板、心跳、告警、日志、报告
sentiment/   可选情绪数据源、综合指数、IC 验证
scripts/     CLI 入口、systemd 安装脚本、git 钩子
config/      system.yaml（全局）与 bots/*.yaml（每个 bot 一个文件）
```

设计详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，密钥处理详见 [SECURITY.md](SECURITY.md)。

## 许可证

基于 [Apache License, Version 2.0](LICENSE) 授权。署名信息见 [NOTICE](NOTICE)。另请阅读[免责声明](DISCLAIMER.md)。

> 本文为英文 README 的中文翻译；如有出入，以英文版为准。
