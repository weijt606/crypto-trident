# 开源发布检查记录

日期：2026-10-20。审查基线：`dd41e8e878f7f736c8ea0db2e18aeb7ec6994dd9`；
下列整改和验证针对该基线上的工作区变更。本记录不是法律或实盘安全认证。

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

## 本地验证证据

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

本次 GitHub 只读核验显示仓库为 private，远程仅有 `refs/heads/main`；现有可达历史、
本地 reflog 和不可达 blob 均未检出密钥，没有为脱敏而重建仓库的证据。
历史中保留旧依赖版本是正常的修复记录，不要求抹掉旧提交。

若将来确认历史包含真实密钥：先撤销/轮换，再按文件和引用范围清理历史，并检查 forks、PR、
日志、制品和缓存；必要时联系 GitHub。删除重建不能保证消除已有 clone、fork 或缓存中的数据。
若发现无权公开的代码或个人数据，也应清理对应历史并处理外部副本，不应只从最新文件删除。
[GitHub 官方敏感数据清理指南](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)

## 个人开源版本的发布定位

维护者明确此版本用于分享个人项目源码、做开源贡献；个人运行环境独立维护。
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
4. 本次本地验证已经通过；准备推送后，建议再确认 GitHub 上的 checks 正常执行。CI 是否启用、
   托管平台是否暂时失败，本身不决定代码能否作为开源软件发布。

以下是维护建议，不是个人开源项目必须配齐的发布门槛：

- 接收外部 PR 后，可配置分支规则要求 checks 通过、阻止强推和删除。无需为单人项目强制要求
  其他审阅者；不要配置不存在的检查项或要求手动 dependency-audit 每次都通过。
- 准备公开时，检查 GitHub 提供的 Secret scanning / Push protection、Dependabot alerts 和
  Private vulnerability reporting。当前 private 仓库的安全配置 API 返回 null，私密报告接口
  返回 404，不能据此宣称这些功能已启用。具体可用性以 GitHub 界面和账户方案为准。
- 实盘运行保障、持续在线监控和自动部署由个人运行环境独立处理，不属于此开源版本的 CI 职责。

仓库转为 public 是独立的发布动作。本次未改变可见性，未推送或删除远程内容。
可以按已记录限制发布源码版本；不要将这些检查宣传为生产资金安全保证、跨交易所实盘兼容认证，
或运营交易服务的法律许可。
