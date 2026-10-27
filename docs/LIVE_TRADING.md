# Live execution notes / 实盘执行说明

The live executors are experimental software for self-hosted, operator-controlled use.
They implement Binance spot and USDT-M perpetual order paths on Linux/macOS. They are not
an exchange recommendation, a financial service offering, a legal authorization or an
execution guarantee. Read [DISCLAIMER](../DISCLAIMER.md), [COMPLIANCE](COMPLIANCE.md),
[SECURITY](../SECURITY.md) and the [architecture](ARCHITECTURE.md) before connecting an account.

实盘执行器是供操作者自行部署和控制的实验性软件，在 Linux/macOS 上实现 Binance 现货和 USDT
本位永续下单路径，不构成交易所推荐、金融服务要约、法律许可或执行保证。接入账户前先阅读
上述免责声明、合规、安全及架构文档。下列命令均在仓库根目录执行。

## Eligibility and support / 使用资格与支持边界

Determine the applicable jurisdiction, exchange legal entity, account eligibility and
product permissions separately for spot and derivatives. Public API access, successful
KYC, a valid trading key or `stage: live` alone does not settle those questions. The code
has no legal-eligibility or suitability check. Do not bypass geographic, identity or product
restrictions. See the jurisdiction examples in [COMPLIANCE](COMPLIANCE.md).

The repository provides software and technical documentation, without a hosted trading
service, custody, managed accounts or personalized investment advice. Bug reports and code
review do not create an account-monitoring or emergency-response service. Never send the
maintainer credentials or unredacted account records. A separate service, paid support,
signals or exchange promotion needs its own scope and legal review; these docs do not
approve it. These statements describe project scope and do not add restrictions to Apache 2.0.

现货与衍生品须分别核对适用地区、交易所法律实体、账户资格和产品权限。公共接口可访问、通过
KYC、交易密钥有效或设置 `stage: live`，单独都不能解决这些问题。代码不进行法律资格或适当性
检查，不应绕过地域、身份或产品限制。各地区示例见[合规边界](COMPLIANCE.md)。

仓库提供软件与技术文档，不包含托管交易服务、资产托管、代管账户或个性化投资建议。缺陷反馈
和代码审阅不构成账户监控或应急响应服务。不要向维护者提交凭据或未脱敏账户记录。另行提供的
服务、付费支持、信号或交易所推广须单独界定范围并审查，本文不予批准。这是项目范围说明，
不增加 Apache 2.0 许可限制。

## Known execution limits / 已知执行限制

| Limit | English | 中文 |
| --- | --- | --- |
| Risk response | Checks run on new closed bars; no exchange-hosted protective stop is installed automatically. Gaps, intrabar moves and outages can exceed thresholds. | 风控按新闭合 K 线检查，不自动设置交易所托管止损；跳空、盘中波动和中断可能使损失超过阈值。 |
| Capital and coordination | `cash_cap` is a local sizing/accounting setting, not an exchange-enforced asset boundary. Bot locks do not coordinate different bots or hosts sharing an account and symbol. | `cash_cap` 是本地规模/记账配置，不是交易所强制资产隔离；bot 文件锁不协调共享账户和交易对的不同 bot 或主机。 |
| Unknown orders | Client-id lookup and fill polling are bounded. Failed lookup can leave an outcome unknown; there is no complete durable order-intent recovery protocol. | 客户端订单号查询与成交轮询次数有限，查询失败可能仍然结果不明；没有完整的持久化订单意图恢复协议。 |
| Perpetual settings | Startup attempts margin/leverage setup. Unsupported or failed read-back is logged and skipped; failed position-mode lookup assumes one-way mode. | 永续启动会尝试设置保证金模式和杠杆；不支持或失败的读回会记录并跳过，持仓模式查询失败时按单向模式处理。 |
| Closing and baseline | Failed closes, partial fills and below-precision residuals can leave exposure. Restoring a pre-existing baseline can increase exchange net exposure while closing the bot's offsetting position. | 平仓失败、部分成交和低于精度的残余可能留下敞口；关闭 bot 的对冲持仓以恢复原有基线时，可能增加交易所净敞口。 |
| Tests and simulation | Fake-exchange tests, a chronological split and paper fills do not establish profitability or live execution safety. | 假交易所测试、时间切分与模拟成交不证明盈利能力或实盘执行安全。 |

A dedicated account/sub-account reduces interference but does not guarantee a loss limit.
Depending on product and account terms, leveraged losses can exceed initial margin. A
stopped process, an exit code or a local ledger cannot prove the exchange account is flat.

专用账户或子账户有助于减少干扰，但不保证损失上限。依产品与账户条款，杠杆损失可能超过初始
保证金。进程停止、退出码或本地账本都不能证明交易所账户已经空仓。

## Reviewed setup / 审阅后的配置流程

The following is a technical sequence for an operator who has independently decided that
live use is permitted. It does not approve any particular account, capital amount or strategy.

以下是操作者独立确认实盘使用获准后的技术流程，不批准任何具体账户、资金规模或策略。

1. Review strategy behavior, fee/slippage assumptions, out-of-sample results and paper
   records. Run the fake-exchange drill: `uv run --frozen pytest tests/test_live_drill.py`.
   No duration of paper operation or passing result proves readiness for real funds.
   审阅策略行为、费用滑点假设、样本外结果和模拟账本，并运行上述假交易所演练；模拟时长和
   测试通过都不证明真实资金运行已安全。
2. Use a dedicated trading key with reading and the required market permission. Disable
   withdrawals, universal/internal transfers, margin borrowing and options; bind an IP
   whitelist. Use a dedicated account/sub-account and avoid other position managers on the
   same account and symbol. The executor requires POSIX file locking.
   使用专用交易 key，开启读取及所需市场权限，禁用提现、通用/内部转账、保证金借贷和期权，
   绑定 IP 白名单。使用专用账户/子账户，避免同账户同交易对有其他持仓管理者；执行器要求 POSIX 锁。
3. Keep keys in `.env` (copy `.env.example`, set `chmod 600 .env`) or an externally managed
   process environment. Environment settings take precedence. Use `umask 077` for manual
   runs. Public data, paper and dashboard commands need no key; `BINANCE_READ_*` is a
   reserved integration helper, unused by bundled commands.
   凭据保存在 `.env`（从示例复制并设置上述权限）或外部管理的进程环境中，环境值优先。手动运行
   前设置 `umask 077`。公开数据、模拟盘与面板无需 key；`BINANCE_READ_*` 是随附命令未使用的预留配置。
4. `uv run --frozen python scripts/verify_trade_key.py` makes authenticated read requests
   and prints non-zero spot balances and estimated values. Keep its output private. Its
   spot-oriented check requires spot trading permission and reports futures permission
   separately; it is not a complete futures-account check or a legal/suitability check.
   The live executor independently checks its required market permissions at startup.
   该命令使用交易密钥执行鉴权读取，并打印非零现货余额与估值，输出应保密。检查偏向现货，要求
   现货交易权限并另行报告合约权限，不是完整合约账户检查或法律/适当性检查；执行器启动时另查所需市场权限。
5. Review `risk:` including the required non-empty `allowed_symbols`. For spot, review
   `live.initial_cash` and `live.cash_cap` in `config/system.yaml`; for perps, bot `cash_cap`
   takes precedence over system `live.cash_cap`. Check balances, open orders, positions,
   position mode, margin mode and leverage at the exchange. Arrange independent monitoring
   and a response plan. Only the human operator may set `stage: live`.
   审阅 `risk:`（包括必需且非空的 `allowed_symbols`）。现货审阅系统的 `live.initial_cash` 和
   `live.cash_cap`；永续优先取 bot 的 `cash_cap`，否则取系统值。独立核对交易所余额、未结订单、
   持仓、持仓模式、保证金模式及杠杆，安排监控与处置方案。仅由人工操作者设置 `stage: live`。
6. The commands below start real execution. The perpetual startup can also change margin
   mode and leverage. A local stage or permission check does not make these commands a dry run.
   以下命令启动真实执行，永续启动还可能修改保证金模式和杠杆；阶段与权限检查不使其成为预览。

   ```sh
   # Choose the command matching the reviewed bot market; these can place real orders.
   uv run --frozen python scripts/run_live.py --bot <name>
   uv run --frozen python scripts/run_live_perp.py --bot <name>
   ```

## Unknown outcomes, halt and recovery / 结果不明、停机与恢复

If an order outcome is unknown, do not treat an unsuccessful lookup as proof of no fill or
blindly resubmit it. Prevent further automated submissions under your operating procedure,
then independently reconcile exchange orders, fills, balances and positions with local
records. Stopping automation leaves exchange-side orders and exposure in place. The
current code does not automatically enforce this complete incident procedure.

A kill or soft-cut is a reason for investigation. Do not delete state/markers or repeatedly
restart to bypass it. `scripts/revive.py` does not query the exchange or close positions:
it removes the kill marker and resets persisted breakers, including drawdown and daily-loss
references for re-baselining at the next mark. `--reviewed` stores an operator statement;
it does not verify that a review happened. Stop the executor, establish order outcomes and
residual exposure, and investigate the halt before deciding whether to reset and restart.

For an independently reviewed recovery, the syntax is
`uv run --frozen python scripts/revive.py --bot <name> --reviewed "<actual review conclusion>"`.
Supply the actual conclusion (at least 10 characters), without credentials or account identifiers.
Do not copy a generic approval statement as evidence. The command writes local state and
an event; restarting live execution is a separate operator action.

`scripts/retire_live_perp.py --yes` can send real closing orders and write a soft-cut marker;
it is not a read-only health check. Without `--yes` it uses authenticated reads and local
startup bookkeeping, but does not send orders or change margin/leverage. Review residual
positions independently, including dust and any restored baseline. There is no equivalent
bundled spot retirement CLI. See [architecture](ARCHITECTURE.md) for closing exceptions.

订单结果不明时，不把查询失败当作未成交，也不盲目补单。按运行预案阻止后续自动提交，再将
交易所订单、成交、余额和持仓与本地记录独立核对。停止自动化仍会留下交易所订单及敞口；当前
代码不会自动强制执行这套完整事故处置流程。

kill 或 soft-cut 需要调查，不应删除标记/状态或反复重启绕过。`scripts/revive.py` 不查询交易所，
也不平仓；它移除 kill 标记、重置持久化熔断状态，并让回撤和日亏损基准在下次估值时重设。
`--reviewed` 只记录操作者说明，不验证审阅确实发生。决定重置或重启前，停止执行器、查明订单
结果及残余敞口并调查停机原因。上述恢复语法须填写真实结论（至少 10 字符），不包含密钥或
账户标识，不把通用批准语句当作证据。命令会写本地状态与事件，重新启动实盘是另一个操作决定。

`scripts/retire_live_perp.py --yes` 会尝试真实平仓并写 soft-cut 标记，不是只读健康检查。不加
`--yes` 时仍有鉴权读取和本地启动记录，但不发单、不改保证金模式和杠杆。须独立核对残余持仓，
包括零头和恢复的基线。当前没有对应的现货退役 CLI；平仓例外见[架构](ARCHITECTURE.md)。
