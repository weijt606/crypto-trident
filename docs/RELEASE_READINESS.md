# 开源发布检查记录

**当前决定：暂缓开源发布（2026-10-20）。** 此前检查结果仅是历史工程证据，不构成当前发布批准。
本轮只在当前副本升级免责声明与信息安全防护，不改变交易风控、不部署、不推送、不变更仓库可见性。
保留实盘功能的发布方案仍待按具体地区、功能与支持方式作法律审查。

日期：2026-10-20。审查基线：`dd41e8e878f7f736c8ea0db2e18aeb7ec6994dd9`；
首次扫描和本地验证针对该基线上的整改；下表保留当时的证据快照。
整改已由提交 [5bdb564](https://github.com/weijt606/crypto-trident/commit/5bdb564fc349062084fa2b61f0752d53e0e6a2fe)
推送至 main，[GitHub CI](https://github.com/weijt606/crypto-trident/actions/runs/39395758634) 的 checks 已通过；
dependency-audit 按手动触发配置跳过。本记录不是法律或实盘安全认证。

## 已完成的整改

- 密钥权限采用三态校验：缺失、异常、危险权限或未绑定 IP 白名单均拒绝实盘启动。
  读取权限、对应市场交易权限必须明确启用。
- 实盘仅允许已实现的 Binance 接入；其他 exchange 配置明确拒绝，避免忽略配置后连接错误场所。
- 无 POSIX 锁的平台拒绝实盘；在 POSIX 上加载交易密钥前检查 `.env` 权限。
- 启动器统一处理交易所及其他执行异常；日志格式化（含 traceback）、告警持久化和推送前统一脱敏。
  覆盖 URL 查询参数、认证头、secret/password、Telegram token 路径及已由所属模块加载的密钥的无标签输出。
- 提交钩子不再回显命中的敏感内容；bot 名称拒绝尾部换行。
- ECharts 从 5.5.0 升级为 6.1.0，校验官方 npm tarball SRI；补全其 NOTICE、d3 和 ZRender
  许可证，记录文件 SHA-256、版本、来源和内置 ZRender 版本。
- 新增最小权限、固定 Actions commit SHA 的轻量 CI：单一 Linux / Python 3.12 环境跑测试、
  完整历史/工作区密钥扫描及内置资源哈希校验；全部锁定 Python 依赖和 npm 漏洞审计手动触发。
  不做定时扫描、自动部署或实盘交易。
- 明确闭合 K 线风控的延迟、无交易所托管止损、Telegram 数据流、第三方内容和权属审查要求。
- Reddit 默认关闭；启用还须确认所需访问批准并提供 User-Agent；确认开关本身不是平台授权。
- 新增 [通用交易所接入方案](EXCHANGE_INTEGRATION.md) 与 [合规边界](COMPLIANCE.md)。
  通用适配器是后续架构方案，不宣称已完成其他交易所实盘支持。

## 首次整改时的本地验证证据

| 检查 | 结果 |
| --- | --- |
| pytest（现有环境：macOS / Python 3.12） | 342 项通过；使用 fake exchanges，不触及真实资金 |
| Gitleaks 8.30.1：所有分支可达历史 | 3 次提交，未检出密钥 |
| Gitleaks：扩大至本地 reflog 历史 | 7 次提交，未检出密钥 |
| Gitleaks：28 个不可达 blob | 未检出密钥；这些对象不是普通分支推送的可达历史 |
| Gitleaks：整改工作区 | 未检出密钥 |
| pip-audit 2.10.1：uv.lock 中 41 个第三方 Python 包 | 未检出已知漏洞，包括其他平台的依赖 |
| OSV：ECharts 6.1.0 / ZRender 6.1.0 | 均未检出已知漏洞 |
| 内置 JS 及许可证 SHA-256 | 与 vendor.json 一致 |
| ECharts 6.1.0 图表兼容性冒烟 | Node SVG SSR 渲染 line、bar、candlestick 通过；不是完整浏览器视觉验收 |
| JS / shell / 工作流 | app.js 语法、hook shell 语法、工作流 YAML 结构及 git diff 空白检查通过 |
| 两张 README 截图 | 未见账号/密钥/服务器信息；无附加 PNG 元数据；README 标明合成演示数据 |

安全扫描可能有漏报；“未检出”只代表此次检查结果，不证明历史从未泄露。
在线漏洞数据库会更新，发布前和后续维护时应重复检查。

本地复查命令（在线检查需要网络；先安装固定版本的审计工具）：

```sh
uv sync --frozen
uv run --frozen pytest
uv run --frozen python scripts/check_vendor.py --online
uv tool run --from pip-audit==2.10.1 python scripts/audit_dependencies.py
gitleaks git --log-opts='--all --reflog' --redact --no-banner
gitleaks dir . --redact --no-banner
```

也可在独立工具环境安装 `pip-audit==2.10.1`，再用该环境的 Python 运行
`scripts/audit_dependencies.py`；CI 采用此方式。该脚本只审计锁文件，不安装/解析项目依赖。

## 是否删除仓库重新创建

当前建议：**保留现有仓库，正常提交整改并推送，不删除、不强推、不重写历史。**

首次整改时的 GitHub 只读核验显示仓库为 private，远程仅有 `refs/heads/main`；现有可达历史、
本地 reflog 和不可达 blob 均未检出密钥，没有为脱敏而重建仓库的证据。
历史中保留旧依赖版本是正常的修复记录，不要求抹掉旧提交。

若将来确认历史包含真实密钥：先撤销/轮换，再按文件和引用范围清理历史，并检查 forks、PR、
日志、制品和缓存；必要时联系 GitHub。删除重建不能保证消除已有 clone、fork 或缓存中的数据。
若发现无权公开的代码或个人数据，也应清理对应历史并处理外部副本，不应只从最新文件删除。
[GitHub 官方敏感数据清理指南](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)

## 个人开源版本的发布定位

维护者明确此版本用于分享个人项目源码、做开源贡献；运行账户与私人配置不属于开源内容。
CI 是帮助复现和协作的维护工具，不是 Apache 2.0 开源许可的前提，也不是生产安全或法律认证。
个人项目持续运行是有价值的使用经验；开源副本仍需验证在没有个人配置和运行数据时能安装、测试。

本仓库的 CI 仅在 GitHub 托管 runner 执行，不配置交易凭据，不接入个人服务器，不安装或重启服务。
推送 main 或提交 PR 时自动运行一个 Linux / Python 3.12 检查任务；从 Actions 页手动运行 CI 时，
额外执行 dependency-audit。发布新版本或修改依赖时建议手动审计一次。以后出现跨平台问题再增加矩阵。

运行 CI 不会部署这些代码到个人环境；个人环境更新执行器前应单独审阅行为变化，尤其是这次新增的
权限字段完整性、IP 白名单、文件锁和 `.env` 权限要求。

## 发布前确认与可选维护措施

源码发布前仍应确认：

1. 发布的是本次已检查的源码和示例，未混入真实密钥、账本、日志或私人配置。
2. 对代码、截图、外部片段及贡献有权公开。代码扫描无法证明不存在雇主或客户权利。
3. 说明当前支持范围和已知限制，并按实际所在地和活动审查 [COMPLIANCE](COMPLIANCE.md)。
   如果将来提供实盘部署、付费支持、代运营、信号或导流，应重新评估对应业务。
4. 首次整改的本地验证和 5bdb564 的 GitHub checks 已通过。后续提交仍应分别检查，不能沿用
   旧提交的结果宣称新版本已通过。CI 是否启用、托管平台是否暂时失败，本身不决定代码能否开源。

以下是维护建议，不是个人开源项目必须配齐的发布门槛：

- 接收外部 PR 后，可配置分支规则要求 checks 通过、阻止强推和删除。无需为单人项目强制要求
  其他审阅者；不要配置不存在的检查项或要求手动 dependency-audit 每次都通过。
- 准备公开时，检查 GitHub 提供的 Secret scanning / Push protection、Dependabot alerts 和
  Private vulnerability reporting。首次检查 private 仓库时，安全配置 API 返回 null，私密报告
  接口返回 404；这些历史返回值不能证明相关功能在以后仍未启用或已经启用。具体可用性以 GitHub 界面和账户方案为准。
- 实盘运行保障、持续在线监控和自动部署由个人运行环境独立处理，不属于此开源版本的 CI 职责。

整改已推送，仓库可见性未由本次操作改为 public；转为公开仍是独立的发布动作。
当前暂缓公开发布；不要将历史检查宣传为生产资金安全保证、跨交易所实盘兼容认证，
或运营交易服务的法律许可。

## 文档核验（2026-10-20）

范围：两份 README、架构、安全、贡献、免责声明、合规、接入方案和本记录共 9 份 Markdown；
另检查 LICENSE、NOTICE、ECharts 组件许可及示例配置说明。以 5bdb564 的实现和相关 CLI 为依据，
修正文档，不通过改动运行逻辑来迁就原有能力描述。

| 发现的问题 | 核验依据 | 修正 |
| --- | --- | --- |
| 所有回测报告都有样本外结果 | scripts/run_backtest.py 的 --oos 分支 | 说明默认 70/30、可关闭、非自动实盘审批 |
| 回测与实盘行为一致、费用始终非零 | backtest/engine.py、execution/paper.py、portfolio.py | 分别说明成交时点、费用假设与模拟边界 |
| 情绪 IC 是强制交易门槛/显著性检验 | sentiment/validate.py、scripts/validate_sentiment.py | 定义为人工解释的启发式研究工具 |
| 只读 key 已用于数据/监控/报告 | read_only_keys 无随附调用方 | 标为预留 helper；公共数据及账本报告无需 key |
| .env 不导出等于所有环境凭据都不被继承 | core/config.py 的环境优先逻辑 | 区分文件读取与已注入环境变量 |
| 启动重放本地历史 | execution/warmup.py | 实为联网拉取，且调用方当前默认 Binance |
| 风控不可绕过、平仓总是 exchange reduce-only | safety/guard.py、live_perp.py 的 baseline 分支 | 区分开发约定、实盘状态、基线恢复及账户敞口 |
| 超时查询必然确认结果、所有交易参数都验证 | common.py 查询失败返回、live_perp.py 回退分支 | 说明未知结果和设置读回/模式查询回退 |
| paper 重启与 live 熔断持久化混为一谈 | execution/paper.py 的状态恢复 | 区分账户恢复与持久化 breaker 恢复 |
| --reviewed 的省略号示例不满足最低长度 | scripts/revive.py | 替换为完整审阅说明示例 |
| 当前费率/资金调整范围描述过宽 | config/system.yaml、monitor/dashboard.py、scripts/report.py | 标为费率假设；面板资金调整不自动用于 CLI 报告 |
| 发布记录仍称未推送/未在 GitHub 验证 | 提交及上方 CI 链接 | 按已完成事件更新，保留初次扫描快照的时间范围 |

外部条款核对保留在 [COMPLIANCE](COMPLIANCE.md) 中的官方链接；文档核验不证明素材权属，
也不替代具体使用行为的法律判断。原始上游许可证文本保持不变。

## Agent 与实盘文档补充

README 增加外部 Agent 操作入口；新增 AGENTS.md 和 AGENT_OPERATIONS.md，区分模型推理、
主机数值计算、数据/交易 API 与常驻进程。未引入模型 SDK、API 凭据或实际 Agent 控制器。
README 的详细实盘配置移至双语 [LIVE_TRADING](LIVE_TRADING.md)，保留准确的支持范围；
文档重新排布不改变现有代码功能，也不构成降低监管风险的保证。

外部参考驱动器另经只读结构核验，文档补充非交互 CLI、提供方环境路由、有限超时/重试和产物核验
这些通用模式。未运行或修改参考项目，也未移植其源码、提示词、私人配置、任务目标或数据；
驱动器和指令队列在本仓库仍标为未实现，不能据此宣传已经具备自动 Agent 运营。

## 本轮信息安全整改

新增索引隐私检查（`scripts/check_repository_safety.py`），在本地提交 hook 和轻量 CI 中检查
Git 索引快照中的私人文件路径、个人机器绝对路径及越界符号链接，不读取外部链接目标，
不输出匹配内容和文件名。检查不替代 gitleaks，不检查 Git 历史，也不会自动审阅截图、
普通业务配置和任意自定义目录中的个人数据。新增 Agent 会话与输出的忽略规则。

免责声明新增强制性法律责任、安全隐私与外部 Agent 风险说明；这不等于取得法律许可。
本轮没有修改交易执行器或交易风险阈值。验证结果见完成报告，历史测试数字不代表本轮结果。

本轮本地验证（2026-10-20，当前未提交工作区）：357 项测试通过，其中索引隐私检查新增 15 项；
106 份当前源码文件（含新增文件）的隐私规则检查通过；101 个现有索引 blob 检查通过；
Gitleaks 工作区扫描未检出密钥；12 份 Markdown 的 77 个本地文件链接和围栏配对检查通过；
内置 vendor 文件完整性、hook shell 语法及 diff 空白检查通过。交易相关的 4 个临时改动文件
均逐字节核对恢复为 HEAD。本轮未重新执行在线依赖漏洞审计、Git 历史扫描或 GitHub CI，
也未安装服务或调用交易接口。新增文件尚未入索引，因此另行检查了当前文件内容；提交时 hook
仍需检查最终索引快照。结果仅适用于本次检查时的版本。
