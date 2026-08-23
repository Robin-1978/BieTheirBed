# 小诺产品剩余 Backlog 方案设计

> 状态：已实施并于 2026-08-24 发布
>
> 日期：2026-08-23
>
> 输入：[小诺产品剩余工作清单](knoa-product-remaining-backlog-2026-08-23.md)
>
> 原则：Node 是工作与执行权威；Hub 是身份、公网投递和连接协调边界；Capability Gateway 是 Agent 行动的唯一安全边界；正向设计、高内聚、低耦合、KISS、YAGNI。

## 0. 实施结果（2026-08-24）

本方案 Phase A-F 已按独立提交完成：可靠启动边界、持久通知 Intent、通用 Capability Bundle、Browser MCP 参考包、Event Source/Webhook 工作流，以及受控改进与签名 Capability Catalog 均已落地。Browser 作为标准第三方 MCP 能力包安装，不在 Core 引入浏览器特例。

最终全量门禁结果：Python `1027 passed`，Mobile Vitest `131 passed`，Python compileall、Mobile TypeScript 和 OpenAPI 契约生成通过。Platform `0.2.48`（生产代码提交 `2b0ca96`）已部署，Hub、Node 和 Lifecycle 均为 `active/running`、`NRestarts=0`；Mobile `0.2.86 (97)` 已使用发布证书和 APK Signature Scheme v2 签名并发布。公网 APK 为 58,302,387 字节，SHA-256 为 `ae4298a9be5e460beda5c3217d94bc949978692185459730f03bd60b82a49f9d`，完整下载与构建产物逐字节一致，Range 下载返回 `206`。

生产 Event Source 已完成真实 HTTPS Webhook 验收：临时设备配对和鉴权、HMAC、重复事件去重、暂停/恢复、Secret Rotation、Node 拉取、2 个事件和 2 次 Task execution 均通过。部署复核同时发现并关闭了两个仅在生产状态下暴露的缺口：Node 控制请求的签名 `audience` 与 Hub 严格模型不一致（`ba64f32`），以及已请求取消的执行在重启恢复为 paused 后不能完成取消（`2b0ca96`）。临时 Event Source、Task、Execution、Session 均已通过公开 API 删除，临时设备均已撤销。

Browser MCP `1.0.0` 已通过签名 Catalog 的显式 `prepare/confirm` 事务安装，Catalog key id 为 `knoa-release-2026-08-24`，Catalog 与安装包 digest 均为 `a7552853cf35d1f66222f09fa56b9a13ff2d0badf3fccacf9f77db0f6d2b68f7`。安装状态为 enabled/healthy，活动配置和重启后的实时 inventory 均包含 10 条 Browser Tool Policy/10 个 Browser MCP Tool；既有 GitLab 5 个、Jira 11 个 MCP Tool 也已迁移为受管配置并在重启后保持可用。

真实 FCM 系统通知仍以 Hosted Hub 配置有效 Service Account 和 Android Firebase 配置为运行前提；本次生产 Hub 未配置 `KNOA_FCM_SERVICE_ACCOUNT_FILE`，Android 工程也没有 `google-services.json`，因此不能伪造真实 killed-App 投递通过。领域 Intent、加密 Token、inbox、重试/永久失效、Token refresh、多设备、退出禁用、过期过滤、重启持久化和 App reconciliation 已由自动化测试覆盖，真实设备远程推送仍是唯一外部运维验收项。

## 1. 决策摘要

本轮不重写已经成立的 Conversation、Task、Agent Runtime、MCP、Artifact、Gateway 或配置控制面，而是在现有边界上补齐产品闭环。

1. 系统级通知采用持久 `NotificationIntent`。Node Core 为工作事件创建事实，Hosted Hub 保存最小投递投影并通过 FCM HTTP v1 投递 Android；飞书、钉钉、桌面和 App 不再从 Task 事件顺序自行猜测通知。
2. 浏览器已有 AI 网页搜索/抓取和桌面工具接入，剩余范围改名为“交互式浏览器会话产品化”。实现为独立、可安装的第一方 Browser MCP 参考包，不在 Core 增加浏览器专用分支。
3. Browser MCP 同时是第三方能力包样板：标准 `capability.yaml + mcp.yaml`、独立进程、显式工具策略、预检、启停、更新和回滚；官方签名证明来源，不自动授予网页写操作权限。
4. 能力包安装继续以 Config Revision 为唯一激活权威。PackageStore 只保存内容，安装事务只编排 inspect、preflight、confirm、publish、health 和 rollback，不建立第二套运行配置。
5. Webhook 与 MCP Resource 共享“事件源”用户流程和状态投影，但不强制合并现有 Adapter、Trigger 或 Resource Repository。Hosted 模式由 Hub 提供稳定公网 Webhook 入口，Node 仍创建并执行 Task。
6. scheduled/event 预检下沉到 `TaskService` 的统一启动边界，不能只放在 Gateway 页面路径。
7. 自我进化仅允许生成、评测和灰度受控候选；权限上限、审批策略、凭据、隔离边界、部署信任根和工具安全等级永远不可自动修改。

## 2. 当前事实与修正后的范围

| Backlog | 已有事实 | 本方案新增范围 |
| --- | --- | --- |
| 远程通知 | Task 通知策略、principal 事件流、App 在线本地通知、飞书 watcher | NotificationIntent、设备注册、Hub 投递箱、FCM、重放/去重/深链 |
| 浏览器 | AI 可调用 `web_search`、`web_fetch`、窗口、截图、键鼠；Codex 通过受控 MCP 使用 Platform Tool | Browser MCP 会话、语义快照、导航、下载、交互、专用 Profile |
| 一键能力包 | PackageStore、Skill/MCP import、inspect、Config Draft、preflight/publish | Capability Bundle、安装事务、健康核验、启停、更新、回滚 |
| Webhook/MCP Resource | WebhookAdapter、Trigger、Resource Bridge、Event Task | 创建向导、稳定地址、密钥、测试、暂停、最近事件 |
| 自我进化 | 评测、候选范围和安全边界已有原则 | 证据、候选、回放、批准、灰度、监控、回滚控制面 |
| 市场与版本 | 内容寻址 PackageStore、版本字段、Release 信任基础 | 签名静态目录、兼容性、来源、版本选择、回滚；不做商业市场 |

浏览器 Backlog 不再描述为“AI 未接入浏览器”。准确边界是：AI 已能研究网页并可间接做桌面操作，但缺少可验证、可恢复、可审计的浏览器语义会话。

## 3. 总体架构与依赖

```text
App / Feishu / DingTalk / CLI / Console
                 |
         User Work / Event Source / Capability UI
                 |
             Gateway / Hub
                 |
       +---------+----------+
       |                    |
Hosted delivery       Node authoritative Core
push / webhook        Task / Intent / Artifact
       |                    |
       |             Capability Gateway
       |                    |
       |          Built-in Tool / MCP Tool
       |                    |
       +----------- Browser MCP
                          Playwright/CDP
                          managed profile/downloads
```

实施依赖固定为：

```text
统一启动预检、竞态修复、诊断合同
          |
          +--> NotificationIntent --> FCM 端到端 --> 渠道收敛
          |
          +--> Capability Bundle --> Browser MCP --> 能力包目录
          |
          +--> Event Source Facade --> Webhook/MCP Resource 用户流程

评测证据合同 ------------------------------------> 自我进化闭环
```

Notification、Browser 和 Event Source 可以独立开发，但发布前共用统一状态、Artifact、审批、审计和恢复门禁。

## 4. 系统级远程通知

### 4.1 所有权

- Node Core 拥有 Conversation/Task/Approval/Interaction 产生的 `NotificationIntent`，它是工作通知事实源。
- Hosted Hub 不推断 Task 状态，只接收 Node 签名的最小 Intent 投影，拥有设备 Token、投递尝试和 Provider 回执。
- Hub 自己拥有的 Node 离线、账户安全和强制更新事件可创建同合同的 Hub-origin Intent，但不能伪装成 Node 工作结果。
- Channel 只做订阅、策略过滤、语言渲染和投递，不复制业务状态机。

### 4.2 Node 数据模型

新增 Node-local 表 `notification_intents`，建议字段：

```text
intent_id                 stable opaque id
principal_id              owner scope
workspace_id / node_id    routing scope
category                  completed | failed | approval_required |
                          interaction_required | node_offline | update_required
work_kind / work_id       task | conversation | node | release
execution_id              optional
semantic_code             stable localized message key
parameters_json           bounded non-secret rendering parameters
deep_link_json            typed route + opaque ids
dedupe_key                unique business transition key
priority                  normal | urgent
expires_at                required for actionable intents
state                     pending | projected | cancelled | expired
source_sequence           monotonic per Node
created_at / updated_at
```

不保存完整 Prompt、模型输出、日志、文件路径、Token 或网页正文。通知只表达“发生什么、影响什么、下一步是什么”。详细内容在认证后的工作详情中读取。

唯一性使用 `(principal_id, dedupe_key)`。例如审批通知以 Approval ID 和 epoch 组成 dedupe key；Task 终态以 Execution ID 和终态组成。状态纠正创建新 Intent 或取消旧 Intent，不覆写已经投递过的历史事实。

### 4.3 Hub 数据模型

Hosted Hub 增加三个投递投影：

```text
push_installations
  account_id, installation_id, provider, token_ciphertext,
  locale, app_version, state, registered_at, last_seen_at

notification_inbox
  intent_id, account_id, workspace_id, node_id, category,
  semantic_code, parameters_json, deep_link_json,
  priority, expires_at, source_sequence, received_at

notification_deliveries
  intent_id, installation_id, attempt, state,
  provider_message_id, next_attempt_at, last_error_code, updated_at
```

设备 Token 使用 Hub 私有密钥加密静态保存；日志只记录 Token 指纹。退出账户、删除账户、设备注销、Token refresh 或 Provider 返回永久失效时立即停用旧记录。

### 4.4 数据流

```text
Task/Approval state commit
  -> 同一 Node 事务追加 NotificationIntent
  -> Node outbound Hub connection 投影签名 envelope
  -> Hub 验证 Node、Workspace、序列和 Intent 幂等键
  -> 写 notification_inbox + delivery rows
  -> FCM adapter 领取、投递、指数退避
  -> App 收到系统通知并按 intent_id 本地去重
  -> 用户点击 typed deep link
  -> App 认证后从 Hub/Node 拉取权威工作状态
```

推送是至少一次投递，不承诺网络层 exactly-once。用户可见的“只提醒一次”由 Hub 唯一投递行、Provider message ID 和 App `intent_id` 去重共同实现。App 打开后通过游标 API 补拉未过期 Intent，修复 Provider 丢包。

### 4.5 Provider 与 API

Android 第一阶段使用 FCM HTTP v1；Provider 位于 Hub adapter，领域合同不出现 FCM 字段。APNs 后续实现同一 `PushDeliveryPort`。

建议 Hosted API：

```text
PUT    /v1/mobile/installations/{id}/push-token
DELETE /v1/mobile/installations/{id}/push-token
GET    /v1/notifications?cursor=...&limit=...
POST   /v1/notifications/{intent_id}/ack
POST   /v1/notifications/test
```

Node 到 Hub 使用现有认证出站连接增加 typed notification envelope/ack，不开放新的 Node 公网端口。FCM Service Account 只放 Hub Secret Store/私有环境文件，不进入 Node、App、配置 diff 或日志。

### 4.6 渠道收敛

- App Push、App 在线通知、飞书、钉钉和桌面 watcher 消费同一 Intent feed。
- 每个渠道保存自己的 delivery cursor，不拥有第二套通知事实。
- Task `notification_policy` 在创建 Intent 前过滤类别；账户/设备渠道开关在投递时过滤渠道。
- 审批过期或已解决后，点击旧通知只展示当前权威状态，不重新打开失效审批。

### 4.7 提交切片与验收

1. N1：NotificationIntent 模型、Repository、Task/Approval 事务钩子和合同测试。
2. N2：Node→Hub 签名投影、Hub inbox 和游标补拉。
3. N3：App installation push token 注册、FCM adapter、重试和失效处理。
4. N4：App 前后台接收、intent 去重、typed deep link、设置页真实测试。
5. N5：飞书/钉钉/桌面切换到 Intent feed，删除基于事件顺序的重复判断。

验收必须覆盖：App 被系统杀死后的完成/失败/审批通知；重复 envelope；Token refresh；多设备；退出账户；Intent 过期；Node/Hub 重启；Provider 临时失败；点击后 Workspace/Node 隔离。

## 5. Browser MCP 参考能力包

### 5.1 定位

Browser 作为独立标准 MCP 包交付，建议目录：

```text
examples/browser_mcp_server/
├── capability.yaml
├── mcp.yaml
├── README.md
├── server package
└── tests/
```

它有三个身份：

1. 官方可选浏览器能力；
2. Browser 会话和 Playwright/CDP 的唯一实现所有者；
3. 第三方作者学习 Capability Bundle、MCP Tools、Resources、Elicitation、Artifact 和权限声明的参考包。

Core、Gateway 和 App 不出现 Playwright、Chromium、DOM 或 Browser 专用业务分支。产品安装入口可以突出显示“浏览器操作”，但最终仍调用通用 Capability Installer。

### 5.2 进程与会话

- MCP 包以 Node 监督的 stdio 子进程运行，不导入 Core interpreter。
- Browser MCP 自己启动受支持的 Chromium，并管理 `browser_session_id`。
- 默认会话使用临时 Profile，Task/Conversation 结束或 TTL 到期后清除。
- 持久 Profile 必须由用户显式创建并命名，按 principal 隔离；不得默认附着用户日常 Chrome Profile。
- Cookie、LocalStorage、证书和下载临时文件只留在 Node；Hub、Relay、App 不保存浏览器凭据。
- 进程崩溃只使 Browser extension 进入 failed，不影响 Core 或其他 MCP。

### 5.3 工具合同

第一阶段只读/低副作用 MVP：

| MCP Tool | 作用 | Effect | Capabilities | Risk |
| --- | --- | --- | --- | --- |
| `browser.session_open` | 创建临时会话或选择已授权 Profile | local_write | mcp | medium |
| `browser.navigate` | 导航到显式 URL | read_only | mcp, network | medium |
| `browser.snapshot` | 返回 bounded accessibility/DOM 摘要 | read_only | mcp | medium |
| `browser.screenshot` | 生成页面证据 | read_only | mcp | medium |
| `browser.download` | 下载一个用户/Agent 明确选择的资源 | local_write | mcp, network | medium |
| `browser.session_close` | 关闭会话并清理临时 Profile | local_write | mcp | low |

第二阶段交互工具：

| MCP Tool | 作用 | Effect | Capabilities | Risk |
| --- | --- | --- | --- | --- |
| `browser.click` | 激活稳定 element ref | external_side_effect | mcp, network | medium |
| `browser.fill` | 向字段写入文本；可能触发页面网络事件 | external_side_effect | mcp, network | high |
| `browser.submit` | 明确提交表单或确认动作 | external_side_effect | mcp, network | high |
| `browser.wait_for` | 等待 URL、元素或下载状态 | read_only | mcp | low |

不提供无边界 `browser.act(script)`、任意 JavaScript、任意 CDP 命令或“自动完成整个网站”的单一工具。静态 ToolPolicy 无法安全判断任意网页动作，因此必须按语义拆分 Tool；高风险动作继续经过现有 ToolStep 审批、参数冻结和执行检查点。

### 5.4 页面内容与 Prompt Injection

页面标题、DOM、Accessibility Tree、脚本输出、下载文件和站点提示全部是不可信证据：

- snapshot 输出使用稳定 element ref、role、name、state 和 bounded text；
- script/style/隐藏大文本默认省略；总节点数、单字段和总字节有上限；
- 页面文本不能覆盖 System、Skill、Tool Policy、Approval 或用户目标；
- 跨域导航、下载、弹窗和新窗口形成显式事件；
- 内网、loopback、metadata IP 和本地文件 URL 默认拒绝，只有显式能力策略可放开指定范围；
- `javascript:`、带 credentials URL、歧义编码和不支持的 scheme 一律拒绝。

### 5.5 下载与 Artifact

Browser MCP 将下载写入包外的 Node 托管下载根，返回相对句柄、文件名、media type、大小和 SHA-256，不向用户界面暴露后台绝对路径。

第一阶段由 Agent 使用既有 `attach` 把已完成下载转成 Artifact。随后扩展通用 MCP result adapter，使所有 MCP 包都能返回受限 managed-file descriptor，由 Platform 统一校验根目录、大小和 digest 后导入 Artifact；该能力不得只为 Browser 写专用分支。

未完成、超限、digest 不符或被安全检查拒绝的下载不进入 Artifact。Task 清理遵循现有 Artifact 引用和 Browser 临时目录保留策略。

### 5.6 参考包验收

- 完全通过标准 MCP Host 启动、发现和调用，Core 无 Browser import/branch；
- 使用通用能力包安装、预检、启用、停用、更新和回滚；
- 能导航静态页、读取语义快照、下载文件并交付 Artifact；
- 页面关闭、MCP 重启和 Task 取消后没有孤儿 Chromium/Profile/下载；
- 登录态不会跨 principal 或临时会话泄漏；
- prompt injection、内网 URL、超大页面、弹窗风暴和下载炸弹有合同测试；
- README 说明第三方包目录、Tool annotations、本地 Policy、Secrets、Resources、测试和发布方式。

## 6. Capability Bundle 一键安装

### 6.1 包格式

在 Skill/MCP 原有 manifest 外增加产品级 `capability.yaml`：

```yaml
schema_version: 1
id: browser
version: 1.0.0
display_name: 浏览器操作
description: 在当前电脑上读取和操作网页
compatibility:
  platform: ">=0.2.0"
components:
  mcp:
    - path: browser-mcp
  skills:
    - path: browser-research
requested_tools:
  - name: browser.snapshot
    effect: read_only
    capabilities: [mcp, network]
    risk: medium
setup_inputs: []
health_checks:
  - kind: mcp_inventory
entry_points:
  - title: 阅读并总结网页
    mode: immediate_or_background
```

Manifest 是安装意图，不是权限权威。第三方声明必须由本地检查结果和用户确认转成 Config Draft Policy；MCP annotations 仍是不可信元数据。官方签名只允许 UI 显示已验证来源和预填官方 Policy 模板，不能绕过 ToolStep。

不允许任意 preflight shell script。预检只使用 allowlisted declarative check：兼容版本、OS/架构、所需命令、端口、Secrets 名称、MCP initialize/inventory、磁盘预算和固定健康调用。确需依赖安装时由签名 Release Bundle 或 OS package manager 完成，不由模型执行 manifest 脚本。

### 6.2 安装事务

```text
Resolve source
  -> verify manifest/signature/digest/compatibility
  -> freeze bytes in PackageStore
  -> inspect Tools/Resources/Prompts
  -> build permission and setup plan
  -> user confirms exact delta
  -> create Config Draft
  -> preflight
  -> publish Config Revision
  -> start/refresh providers
  -> bounded health verification
  -> commit installation projection
```

失败时：

- publish 前失败只删除未引用 staging；
- publish 后 provider 启动/健康失败，内部重新应用安装前 Config Revision；
- 回滚失败时保留 fail-closed disabled 状态，并显示人工恢复动作；
- 正在运行的 Invocation 保留开始时冻结的 provider/config generation，新 Invocation 使用新版本。

`capability_installations` 只保存展示和编排投影：capability id/version、package digests、active config revision、previous revision、health 和时间。真正激活状态仍由 Config Revision 决定。

### 6.3 用户动作

- 安装：选择本地包、官方目录项或未来的可信 URL；不填写 server id。
- 预检：展示“会读取什么、会修改什么、需要哪些凭据、哪些 Tool 被拒绝”。
- 启用/停用：发布一个新的 Config Revision，不删除包内容。
- 更新：并存新旧内容 digest，健康通过后切换；失败自动回旧 digest。
- 回滚：只允许回到本机已有且验证过的版本；权限扩大仍需重新确认。
- 卸载：先停用并确认没有运行 Invocation，再删除未被 revision/rollback 引用的包。

## 7. Webhook 与 MCP Resource 用户流程

### 7.1 Event Source Facade

新增只读/命令 Facade，不合并底层模型：

```text
EventSourceSummary
  source_id, kind(webhook|mcp_resource), display_name,
  task_id, state, health, last_event_at, recent_outcomes

EventSourceCommand
  create, pause, resume, rotate_secret, test, delete
```

Facade 调用现有 TriggerService、MCPResourceTaskBridge、TaskService 和配置控制面。App 不直接编辑 `webhook_routes`、Trigger ID 或 MCP server id。

### 7.2 Webhook Hosted 路径

Hosted 模式使用稳定地址：

```text
https://<hub>/hooks/v1/<opaque-route-id>
```

创建流程：

1. 用户选择目标 Task/Agent、目标说明和通知策略；
2. Node 创建 Event Task Definition 和 Trigger binding；
3. Hub 创建随机 route id 与 256-bit signing secret，secret 只展示一次；
4. Hub 验证 `X-Knoa-Event-Id`、HMAC、时间窗、body 大小和账户/Workspace/Node 绑定；
5. Hub 持久保存 bounded ingress envelope，在 Node 离线时等待；
6. Node 出站连接领取 envelope，写入现有 durable TriggerEvent，并按 external event id 去重；
7. Node ack 后 Hub 清理/保留短期审计投影。

Hub 是公网投递适配器，不解释 payload、不创建 Task、不运行 Agent。Self-hosted/no-Hub 模式继续支持 loopback WebhookAdapter + 用户自有 TLS ingress，UI 必须明确前置条件，不能伪造可用公网地址。

### 7.3 MCP Resource 路径

1. 从当前 Node 已启用 MCP 的 Resource inventory 选择 exact URI 或明确 prefix；
2. UI 显示 Server 友好名、Resource 描述、最近健康和不可信内容提示；
3. 创建已有语义的 Event Task Definition + Trigger binding；
4. “测试”先 read Resource、执行 preflight 并显示将创建的事件摘要；用户确认后才产生测试 Execution；
5. pause 只暂停 binding/Trigger，不停整个 MCP provider；
6. recent events 显示 received、deduplicated、blocked_preflight、running、completed、failed、dead。

### 7.4 API 与密钥

建议 Gateway API：

```text
GET    /v1/event-sources
POST   /v1/event-sources
GET    /v1/event-sources/{id}
PATCH  /v1/event-sources/{id}/state
POST   /v1/event-sources/{id}/test
POST   /v1/event-sources/{id}/rotate-secret
GET    /v1/event-sources/{id}/events
DELETE /v1/event-sources/{id}
```

Secret value 只进入 Hub/Node Secret Store；App 仅在创建/轮换响应中显示一次。轮换使用短重叠窗口和两个 key version，过期版本自动失效。复制示例包含 Event ID、timestamp 和 HMAC 计算，不把 secret 放 URL。

## 8. 自我进化闭环

### 8.1 受控对象

```text
ImprovementEvidence
  explicit_feedback | failed_execution | recovery_result | metric_regression

EvaluationCase
  sanitized input, expected invariant/outcome, fixture refs

ImprovementCandidate
  kind, base_version, proposed_version, diff, rationale, author

ReplayRun
  candidate, dataset version, safety/cost/quality results

Promotion
  approved_by, scope, rollout state, metrics, rollback target
```

候选状态：`draft -> evaluated -> awaiting_approval -> canary -> promoted`，任何阶段可进入 `rejected` 或 `rolled_back`。所有状态持久化并记录版本、证据和审批人。

### 8.2 允许与禁止

允许候选：Prompt、Skill 内容、Tool 路由、失败恢复建议、缓存参数、模型 fallback、能力说明和非安全阈值。

禁止候选：Tool capability ceiling、effect/risk、审批策略、Secret、系统 Prompt 安全边界、Sandbox、ResourceGrant、信任根、部署脚本、Hub/Node 身份和外部副作用幂等规则。

禁止对象即使人工批准也不能通过 evolution API 发布；它们只能走原本的显式配置/发布流程。

### 8.3 回放与灰度

- 原始用户数据先按 principal 本地保留，生成评测 Case 时脱敏并显式选择是否纳入长期评测集；
- 回放默认禁止真实网络写 Tool，使用 recorded Tool result、fixture MCP 或 sandbox provider；
- 门禁同时比较质量、安全违规、成本、延迟和恢复成功率，不只比较一个总分；
- 单用户产品的 canary 是“明确选择的后续工作/模板”，不是暗中随机改变全部工作；
- 监控超阈值自动停止 canary 并回到 base version，但不会自动批准新候选。

第一阶段只做 Prompt/Skill 候选和离线回放；模型路由与恢复策略在合同稳定后再加入。

## 9. 能力包目录与版本治理

市场依赖第 6 节安装事务完成。第一版只是签名静态 Capability Catalog：

- 官方和用户显式添加的 catalog source；
- catalog entry 包含 id、version、平台兼容范围、package digest、签名、来源、权限摘要和下载地址；
- Release Trust Root 验证目录和包签名；下载后再次验证 content digest；
- 默认不自动更新，不允许未经确认扩大权限；
- 支持 pinned、latest-compatible 和 explicit version；
- 本机至少保留当前与上一个健康版本；
- 已撤销版本禁止新安装，现有安装显示风险并要求用户决定；高危撤销可默认停用但必须保留审计。

第一版明确不做：用户上传、评分评论、支付、组织审批、多租户开发者后台、任意依赖求解、通用 Workflow DSL 和自动安装未审计 native binary。

## 10. 中项设计

### 10.1 握手阶段细分打点

定义通用 `TransportDiagnosticEvent`：scope、attempt id、transport、stage、started/ended、outcome、reason code、request id。Stage 固定为 `dns/tcp/tls/mdns/ice/relay_ticket/relay_socket/relay_crypto/business/server/render`。

React Native 或具体 Transport 无法观测的阶段返回 `unavailable`，禁止用总耗时倒推虚假 DNS/TLS 数据。记录按 account/workspace/node 隔离，只保留最近 bounded 样本；URL query、Token、SDP、IP 全量和业务 payload 不进入诊断。设置页用瀑布图/列表展示最近请求，支持清除与复制脱敏摘要。

### 10.2 scheduled/event 统一预检

新增 Core-owned `TaskLaunchPreflightService`，由 manual、immediate、schedule、webhook 和 MCP Resource 的统一 `execute_bound_launch` 边界调用。Gateway 预检只用于提前展示，不能成为安全权威。

策略：

- blocked：不启动 Agent，持久记录 `blocked_preflight` launch outcome 并创建通知 Intent；
- transient unavailable：按 Schedule/Trigger 现有 retry/backoff 有界重试；
- warning：无人值守执行只有存在与当前 check digest 匹配的用户 acknowledgement 才可继续，否则阻塞并通知；
- 配置或能力 generation 变化使 acknowledgement 失效；
- 测试触发也走同一边界。

### 10.3 文件夹级输入

MVP 使用 Android Storage Access Framework 选择目录，递归生成不可变 `FolderManifest`，保留相对路径，不保存原始 content URI。默认上限建议 200 个文件、总计 512 MiB、单文件沿用 Artifact 上传上限；超限先展示统计并允许用户缩小范围，不静默截断。

上传逐文件使用幂等 ID、进度、取消和精确重试；Manifest 最后提交，只有所有必需文件可用时 Task 才引用该文件夹快照。目录变化不会修改已创建 Task 的输入。Node 本地目录属于另一种 Directory Grant，不与手机上传快照混成一个字段。

### 10.4 Chat 编辑重发与回到最新

失败后“编辑并重发”只把原用户输入和有效附件复制回 composer，发送时创建新 Turn/client request id，不修改历史 Turn。取消/失败回复保留原审计和未知副作用提示。用户离开底部阈值后停止自动滚动并显示“回到最新”；新消息计数清零后恢复跟随。

### 10.5 执行详情统一摘要

复用 `resultSummaryPresentation` 的纯展示合同，抽成 `WorkResultSummary`：完成项、未完成项、证据、Artifact、恢复动作和下一步。“修改清单”只能来自 ToolStep/Audit/Artifact 的结构化事实，不从模型自由文本猜测。一键继续创建同 Task 的 `follow_up` Execution，继承 Agent/Policy，但使用新幂等 ID。

### 10.6 Console 一键修复

诊断项返回 allowlisted `repair_action_id`、影响、effect/risk、是否需重启和 recheck id。Console 只能调用固定 Repair Registry，禁止把诊断文本拼成 Shell。修复仍通过 owner authentication、Tool/Lifecycle 固定动作、审计和必要确认；执行后自动重新检查，失败时提供脱敏日志。

### 10.7 Console 版本信息

统一显示四个事实：安装 Platform 版本、运行进程版本、installation state 的 source commit、ConfigurationService active revision。普通概览只显示一致/不一致；commit、revision、路径和 component generation 放高级诊断。版本数据由进程和配置权威读取，不从 UI 缓存推断。

### 10.8 链路切换历史

在现有 process-local request probe 上增加按 scope 持久的 bounded switch record：from/to、reason code、attempt id、发生时间、失败 stage 和承载的下一个 request id。只记录实际切换，不记录后台探测噪声；最多保留最近 50 次或 7 天，切换账户/解绑时按现有缓存规则清理。

## 11. 已知竞态与测试平台

### 11.1 stop-while-starting 竞态

正确语义不是让测试继续轮询 `runtime.requests`，而是消除 RUNNING 与可取消 Invocation 之间的空窗：

```text
claim Attempt
  -> 注册 active invocation + cancellation token
  -> 持久化 RUNNING
  -> 再进入 Agent Runtime
```

`stop()` 先持久化取消意图，再触发 token。Runtime 尚未进入时，执行器在调用前消费取消；已经进入时发送 interrupt；结果未知时仍遵循 ToolStep fail-closed。测试使用显式 `runtime_entered` barrier，不依赖列表长度或无界 sleep。

### 11.2 Windows 测试

将 POSIX-only 测试按真实能力加 marker/adapter contract，不以 Windows 失败为常态基线：

- 路径、chmod、process group 和 signal 行为进入 OS adapter tests；
- 通用领域测试不得依赖 POSIX 路径字符串；
- GBK 子进程输出在 process adapter 统一解码/替换并保留原始 bytes 摘要；
- Linux/Windows 各跑支持矩阵，核心领域合同必须双平台通过。

## 12. 实施顺序与独立提交

### Phase A：统一可靠性边界（已完成，`81218ff`）

1. `task-launch-preflight-core-boundary`
2. `task-stop-starting-cancellation-latch`
3. `transport-stage-diagnostics-and-switch-history`
4. `console-runtime-commit-config-revision`

完成标准：所有 Task launch path 都不能绕过预检；stop race 有确定状态机；诊断不伪造阶段耗时。

### Phase B：后台通知闭环（已完成，`ac566fc`）

1. `notification-intent-domain`
2. `notification-node-hub-projection`
3. `hosted-push-installation-and-fcm`
4. `mobile-push-deeplink-reconciliation`
5. `channel-notification-intent-cutover`

完成标准：App 被杀死仍能收到完成、失败和审批通知，重启/重复投递/Token refresh 不产生重复用户动作。

### Phase C：通用能力包基础（已完成，`c9add91`）

1. `capability-bundle-schema-and-validation`
2. `capability-install-plan-and-confirmation`
3. `capability-health-rollback-and-version-actions`

完成标准：一个无 Browser 特例的测试 MCP 包可一键安装、停用、更新和回滚。

### Phase D：Browser MCP 参考包（已完成，`2a27f33`）

1. `browser-mcp-session-navigation-snapshot`
2. `browser-mcp-download-artifact-delivery`
3. `browser-mcp-interactive-tools-and-policy`
4. `browser-capability-entry-and-reference-docs`

完成标准：Browser 通过 Phase C 通用机制安装；第三方可复制样板创建另一个 MCP 能力包。

### Phase E：事件源与工作交付（已完成，`f3431ab`）

1. `event-source-facade`
2. `hosted-webhook-ingress-outbox`
3. `mobile-event-source-wizard-and-history`
4. `work-result-summary-and-follow-up`
5. `chat-edit-resend-folder-input`

### Phase F：长期治理（已完成，`7fba63c`）

1. Prompt/Skill 离线候选和回放；
2. 人工批准、显式 canary 和回滚；
3. 签名静态 Capability Catalog；
4. 真实使用验证后再决定是否扩展市场功能。

## 13. 全局发布门禁

- Python 全量、Mobile typecheck/Vitest、Browser MCP 独立测试通过；
- Android 签名构建、Hub/Node 健康和更新下载地址通过；
- Node/Hub/App 任意一方重启后 Intent、Trigger、安装事务和 Browser 临时资源均能收敛；
- 所有新表按 principal/workspace/node 做所有权检查、迁移和备份恢复验证；
- 所有 MCP Tool 的 effect/capabilities/risk 来自本地 Policy，Server annotations 不授予权限；
- 高风险浏览器写操作、能力包权限扩大和外部副作用继续 fail-closed；
- 普通 UI 不出现 server id、内部路径、Profile 目录、Token、Config digest 或底层 Runtime 名词；
- 每个提交可独立回滚，不在一个提交同时引入领域模型、Provider 和 UI 全链切换。

## 14. 明确不做

- 不新增 Browser 微服务或 Core Browser domain；
- 不把 Browser 作为特殊 Built-in Tool 绕过 MCP/Capability Gateway；
- 不直接控制用户默认 Chrome Profile；
- 不允许任意 JavaScript/CDP/manifest preflight script；
- 不新增通用 Workflow DSL、分布式消息队列或企业治理中心；
- 不把 Hub 变成 Task/Conversation 第二写权威；
- 不让推送 Provider、Webhook 或 MCP Server 根据事件自行判断业务终态；
- 不在一键安装中自动信任第三方权限声明；
- 不在自我进化中自动扩大权限或发布未经人工批准的候选。

## 15. 兼容、迁移与回滚

- 所有数据库变化使用 additive migration；新版本先建表/索引再启用 producer，旧字段和旧事件 feed 在消费者切换完成前保留。
- Notification 上线采用 `intent producer -> Hub inbox -> App reconciliation -> push -> channel cutover` 顺序。任一步关闭 feature flag 后，现有 App 在线通知仍可工作；不得同时产生两条用户可见提醒。
- 不支持 push 的旧 App 忽略新 API；Hub 只向已注册有效 Token 的 installation 创建 delivery。
- Capability Bundle 是原有 Skill/MCP import 的上层编排；旧包继续可用。回滚 Bundle 不删除 PackageStore 内容，只发布 previous Config Revision。
- Browser MCP 更新时旧 session 不跨 provider generation 迁移：短任务等待结束，强制更新则明确取消并保留已产生 Artifact；新 session 使用新版本。
- Hosted Webhook 上线前现有 loopback WebhookAdapter 保持可用；同一 route 只能选择一个 ingress owner，防止双投递。
- 自我进化控制面初始为空，不从历史 Conversation/Task 自动抽取长期评测数据；只有显式反馈或脱敏导入进入新模型。
- 回滚发布必须同时验证 Hub/Node schema 向后兼容。若新版本已写入旧版本不认识但可忽略的记录，旧版本只能停止 producer，不得破坏或清空这些记录。
