# 合规边界与数据流 / Compliance and data flows

全球发布边界核对日期：2026-10-28；2026-10-30 复核下列 NFA CTA、MiCA 第 59 条、FCA、
中国大陆通知、Reddit 与 Alternative.me 官方来源。本文件记录发布与运行需要审查的事项，不构成对特定个人、地区或商业模式
的法律许可。与 [DISCLAIMER](../DISCLAIMER.md) 一起阅读；不增加 Apache 2.0 许可证之外的使用限制。

## Publication and service boundaries / 源码发布与服务边界

The current scope retains experimental live executors for self-hosted operation. Publishing
source code and providing trading services are different activities; this distinction is a
review framework, not a finding that publication is exempt everywhere. Applicable duties
depend on the publisher, operator, customers, locations, products and actual conduct.
“Global”, “free”, “research” and “open source” are not regulatory permissions.

This repository supplies software and technical documentation. It does not include hosted
execution, custody, account management or personalized investment advice. Technical support
must not request users' trading credentials or take control of their accounts. Any separate
service must be assessed on its own facts. Apache 2.0 rights are unchanged; this document
neither grants a financial license nor adds a software-use restriction.

当前范围保留供操作者自行部署的实验性实盘执行器。源码发布与交易服务是不同活动；这种区分是
审查框架，不代表任何地方都豁免源码发布。适用义务取决于发布者、操作者、客户、所在地、产品
和实际行为。“全球”“免费”“研究”“开源”均不是监管许可。

仓库提供软件与技术文档，不包含托管执行、资产托管、账户管理或个性化投资建议。技术支持不应
索取交易凭据或控制用户账户；另行服务应按实际内容单独评估。Apache 2.0 权利不变，本文既不
授予金融许可，也不增加软件使用限制。

| Activity / 活动 | Review needed / 审查重点 |
| --- | --- |
| Source, generic examples, bug fixes / 源码、通用示例、缺陷修复 | Accurate capability and risk disclosures, provenance, privacy and applicable publication rules; no blanket exemption assumed. / 准确披露能力与风险、权属、隐私及适用发布规则，不预设全面豁免。 |
| Installation and paid technical support / 安装与付费技术支持 | Define access, responsibilities and promises; assess whether actual work becomes advice, order execution or account control. / 界定访问权限、责任和承诺，判断实际工作是否构成建议、执行或账户控制。 |
| Personalized parameters, signals, copy trading / 个性化参数、信号、跟单 | Review investment-advice, derivatives and promotion rules, including any available exemptions. / 审查投资建议、衍生品与推广规则及可能适用的豁免。 |
| Hosted bots, user keys, discretionary trading, performance fees / 托管 bot、接收密钥、全权交易、业绩分成 | Review authorization/registration, custody or trading authority, security, conflicts and client obligations before offering the service. / 提供前审查许可或登记、资产托管或交易控制、安全、利益冲突与客户义务。 |
| Exchange referrals or performance marketing / 交易所导流或业绩宣传 | Review promotion restrictions, compensation disclosures and product/audience eligibility; disclaimers alone do not resolve these. / 审查推广限制、报酬披露、产品及受众资格，不能仅依靠免责声明。 |

## Jurisdiction examples / 地区审查示例

These are selected official references checked on 2026-10-28, not a worldwide legal survey
or a conclusion about Trident's regulatory status. Check current rules before changing the
service, audience, location or products. Spot and derivatives need separate classification.

下列为 2026-10-28 核对的部分官方参考，不是全球法律调查，也不判定 Trident 的监管身份。服务、
受众、地区或产品变化前应重新核对现行规则；现货与衍生品须分别分类。

- **United States / 美国.** NFA describes CTAs as persons or organizations advising others
  for compensation or profit about futures, futures options, certain forex or swaps;
  advice may be direct or through publications. A paid strategy or managed bot needs
  analysis of instruments, conduct and registration/exemptions, not an assumption that
  delivering software removes those questions. This definition does not decide every
  spot-asset or securities-law question.
  NFA 的 CTA 定义包括为报酬或利润提供上述产品交易建议，直接建议与出版物等间接建议均可能
  涉及。付费策略或代管 bot 须分析产品、行为和登记/豁免，不能因通过软件交付就跳过；该定义
  不解决所有现货资产或证券法问题。
  [NFA CTA definition](https://www.nfa.futures.org/members/cta/index.html)
- **European Union / 欧盟.** MiCA Article 59 addresses authorization to provide in-scope
  crypto-asset services; Article 81 imposes suitability and other duties for advice and
  portfolio management. MiCA excludes crypto-assets qualifying as financial instruments
  under Article 2(4)(a); derivatives may require a different financial-services analysis.
  These provisions do not establish that a generic source-code publisher is a CASP or
  that every crypto product is covered by MiCA.
  MiCA 第 59 条涉及范围内服务的授权，第 81 条对建议和组合管理设有适当性等义务；第 2(4)(a)
  条排除属于金融工具的加密资产，衍生品可能须另按金融服务规则审查。这些条文不证明通用源码
  发布者就是 CASP，也不代表所有加密产品都受 MiCA 规管。
  [Article 59](https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/mica/article-59-authorisation),
  [Article 81](https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/mica/article-81-providing-advice-crypto-assets),
  [Article 2](https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/mica/article-2-scope)
- **United Kingdom / 英国.** FCA states that cryptoasset firms marketing to UK consumers
  fall within the financial-promotions regime even when overseas. Whether a particular
  README, demo, referral or communication constitutes a regulated promotion depends on
  its content and circumstances; a disclaimer is not an automatic exemption.
  FCA 指出面向英国消费者营销的加密资产业务即使位于境外也受金融推广制度约束。具体 README、
  演示、导流或沟通是否构成受规管推广取决于内容和情形，免责声明不是自动豁免。
  [FCA marketing guidance](https://www.fca.org.uk/firms/cryptoassets/marketing-uk-consumers)

For other markets, separately check local advice, execution, custody, promotion and product
rules. A venue's legal entity and account/product availability matter; a Binance API path
or CCXT listing establishes neither legal access nor affiliation. Public API availability,
KYC and key permissions are not substitutes for that review. The software has no legal
eligibility, suitability or geographic enforcement mechanism and provides no bypass procedure.

其他市场应分别审查建议、执行、托管、推广和产品规则。交易所法律实体与账户/产品可用性很重要；
Binance API 路径或 CCXT 收录不证明合法访问或关联关系。接口可用、KYC 和密钥权限不能代替审查。
软件没有法律资格、适当性或地域强制机制，也不提供绕过方法。

The earlier mainland-China references remain relevant only if an activity has that nexus;
they do not determine the status of other markets. The 2026-02-06 notice and accompanying
explanation are preserved here for regional review, not as a global publication test.
此前的中国大陆参考仅在活动涉及该地区时另行审查，不决定其他市场的判断；保留相关原文供地区
审查使用，不作为全球发布标准。
[Notice / 通知](https://www.csrc.gov.cn/csrc/c100028/c7614318/content.shtml),
[Official explanation / 答记者问](https://www.csrc.gov.cn/csrc/c100028/c7614320/content.shtml)

## Public demonstrations / 公开演示

Distinguish personal agent setups and strategies from the tools actually included here.
Label synthetic demos, paper runs and live records accurately. Keep the README's synthetic-data
disclosure with shared demo images; the current PNGs do not carry a synthetic-data watermark.
For personal live screenshots, redact account identifiers and operational details, identify the
period and relevant cost, leverage and capital-flow assumptions, and avoid presenting the
result as evidence of the bundled example's profitability or a promise to users. A disclaimer
does not by itself resolve promotion restrictions; assess the audience and actual communication.

个人 Agent 环境和策略应与本仓库实际包含的工具区分。合成演示、模拟盘和实盘记录须准确标识。
分享演示图时应附上 README 的合成数据说明，现有 PNG 本身没有合成数据水印。
个人实盘截图应遮挡账户标识和运行信息，说明期间及相关费用、杠杆和资金变动口径；
不能用个人结果证明随附示例盈利或向使用者承诺收益。免责声明本身不解决推广限制，须结合受众和实际内容审查。

## 数据和隐私

| 数据流 | 发送或保存在何处 | 操作者注意事项 |
| --- | --- | --- |
| API 凭据与签名请求 | 本地 `.env` / 进程环境及所连接的交易所 | 只读与交易密钥分离；禁提现、转账、借贷、期权；IP 白名单；专用子账户 |
| 行情和情绪请求 | 配置的数据源、默认交易所及 Alternative.me | 提供方可见请求 IP、User-Agent 和查询参数；遵守其访问及再分发条款 |
| 出口 IP 查询 | 鉴权失败时请求 api.ipify.org | 本地日志可能包含完整出口 IP；告警中的 IP 已掩码 |
| 外部 Agent / 模型 API（由操作者使用时） | 由外部工具选择发送给提供方的提示词、代码或摘要；本仓库无内置模型请求 | 避免发送凭据、私人账本与原始日志；审阅外部工具的数据处理条件，项目不会自动隔离这些访问 |
| 告警 | 默认本地 storage；配置后额外发送 Telegram | 可能包括 bot 名、交易对、资金规模、数量、价格、盈亏与风险信息；不用推送则留空 Telegram 设置 |
| 账本、状态、日志和报告 | 本地 storage/、logs/ 或操作者配置目录 | 含个人财务信息；默认目录已 gitignore，覆盖目录需另行排除；手动运行用 `umask 077`，分享前人工审阅 |
| Dashboard | 本机回环 HTTP 接口 | 无登录，仅适合本机或 SSH 隧道；不要通过反向代理或公网转发直接公开 |
| Reddit / 新闻标题 | 主动启用后写本地情绪数据 | 可能包含第三方内容及个人信息；只取必要字段，限制留存，不随源码/镜像分发 |

公开数据、模拟盘、面板和账本报告不需要 API key。`BINANCE_READ_*` 及只读加载 helper 是预留
接口，当前随附脚本未使用；不要把预留配置理解为已实现的私有账户监控。

Reddit 默认关闭；即使配置了该源，仍需明确设置访问批准确认和描述性 User-Agent 才会请求。
确认开关不是 Reddit 的批准，RSS/JSON 可访问也不是授权证明；原始实现并非通用 OAuth 接入，
不满足实际批准条件时保持关闭。不要以 RSS/JSON 回退绕过平台拒绝。
[Reddit 官方规则](https://support.reddithelp.com/hc/en-us/articles/360043512931-Don-t-break-the-site)

Alternative.me 数据须在展示处注明来源，面板已标注。公共接口可访问不等于数据可任意再分发。
新闻 RSS 的个人使用说明也不是对第三方权利的保证。
[Alternative.me 规则](https://alternative.me/crypto/fear-and-greed-index/)

## 权属与第三方软件

发布者应确认对自有代码、截图、文档及贡献具有授权权限，特别是雇主/客户成果、复制片段和外部
素材。没有来源记录的材料应先查明来源，不应仅换个署名或新建仓库后发布。

本项目为 Apache 2.0；内置 ECharts 及其组件的许可见 `monitor/static/ECHARTS_LICENSE` 和 `NOTICE`。
Python 依赖从锁文件安装，其各自许可仍适用；分发 Docker、安装包、wheel 或可执行程序时，
需要另行核对随包组件的许可文本和通知，不应把所有依赖统一标记为 Apache 2.0。

## Release status and effect of statements / 发布状态与声明效力

Live functionality is retained and the source repository is public. Public availability and
document updates do not establish legal eligibility or approve deployment or live trading.
Tests and scans are engineering evidence, not legal or real-money safety certification.
The [release record](RELEASE_READINESS.md) distinguishes historical and current evidence.

Apache 2.0 Sections 7–9 address warranty, liability and accepting additional obligations,
subject to their terms and applicable law. Describing operator responsibilities does not
transfer the publisher's own legal duties or exclude liability that cannot be excluded.
Actual advice, control, marketing and promises matter even when a document labels the
project as software. Seek qualified jurisdiction-specific advice before offering a separate
financial service; this document cannot resolve that service's status.
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt)

保留实盘功能，源码仓库已公开；公开可访问和文档更新不证明法律资格，也不批准部署或实盘交易。测试与扫描
是工程证据，不是法律或真实资金安全认证；[发布记录](RELEASE_READINESS.md)区分历史与本轮证据。

Apache 2.0 第 7–9 条在其条款和适用法律下规定担保、责任及额外义务。说明操作者责任不转移
发布者自身法定义务，也不排除不得排除的责任。即使文档将项目定位为软件，实际建议、控制、
推广和承诺仍影响判断。另行提供金融服务前应取得适用地区的专业意见，本文不能确定其法律身份。
