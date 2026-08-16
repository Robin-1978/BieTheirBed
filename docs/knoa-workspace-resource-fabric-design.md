# Knoa Workspace 资源织网与共享执行架构设计

> 状态：Critic 修订后的权威目标架构；V1 范围已收敛
>
> 日期：2026-08-16
>
> 范围：Account、Workspace、HubService、NodeHost、WorkspaceNode、本地 LLM 共享、配置权威、远程调用、Secret 与后续资源共享
>
> 关系：建立在 `knoa-agent-runtime-design.md`、`knoa-configuration-control-plane-design.md`、`knoa-extension-model-hub-node-design.md` 和 `knoa-module-architecture.md` 之上；涉及 Hub/Workspace 术语、共享资源归属、跨 Node 模型调用和 Workspace/Node 配置所有权时，以本文为准
>
> 设计取向：正向设计；高内聚、低耦合；local-first、Hub-assisted、self-hostable；KISS、YAGNI；不保留与目标模型冲突的旧配置兼容层

## 1. Critic 结论与修订摘要

原设计的方向成立：Account 应是身份，Workspace 应是共享资产和授权边界，Node 应是部署和执行
权威，本地 Qwen 模型应可以被同 Workspace 的其他 Node 使用。

Critic 指出的以下问题成立，本文已经修正：

1. 无 Hub 模式下 Workspace 缺少权威所有者；
2. Workspace Revision 与 Node ManagedConfig 对相同字段形成双写；
3. 现有“逻辑 Hub”与新增 Workspace 是重复租户聚合；
4. Hosted Hub 能否读取 Agent Prompt、Skill 和 MCP 定义没有明确决定；
5. Hub 被要求校验 Node-local Principal，形成双重权限权威；
6. 远程模型调用缺少 admission、attach、reconcile 和 outcome unknown 状态机；
7. 第一阶段提前建设了通用 Asset、Binding、Vault、Virtual Node 和跨 Workspace 市场模型；
8. Hub 与 Node 双侧精确 quota 会形成分布式额度权威。

修订后的 V1 只验证一个纵向价值闭环：

```text
Personal Workspace
  + Node A 上的 Qwen 3.5 4B ModelDeployment
  + Node B 上的 Knoa Agent
  + WorkspaceNode 级 ResourceGrant
  + direct 优先 / Relay fallback 的幂等远程模型调用
```

Agent、Skill、MCP、Tool、Virtual Node、Workspace Vault 和跨 Workspace 分享仍保留正确方向，但不
进入 V1 最小表、API 和 UI。

## 2. 最终核心决策

```text
AccountSubject       身份
Workspace            唯一逻辑租户、共享资产与授权边界
HubService           可选的身份、目录、Relay 和密文投递服务
WorkspaceNode        单 Workspace 的逻辑执行租户
NodeHost              安装 Knoa 的物理机器/安装实例
WorkspaceRegistry    明文共享定义的唯一写入权威
```

必须遵守：

1. `HubService` 是服务部署，不再是第二个资源租户；原文档中的“逻辑 Hub”迁移为 Workspace；
2. Account 与 Workspace 通过 Membership 建立多对多关系；
3. Account identity 必须带 issuer，V1 不做跨 Hub 身份联邦；
4. V1 一个 NodeHost 只运行一个 WorkspaceNode；
5. Workspace-managed 字段与 Node-local 字段按 schema 分区，不允许双写；
6. Hosted Hub V1 不读取 Workspace 明文配置，只保存 metadata、digest、密文候选和观察状态；
7. Node A 是模型执行事实权威，Hub 不是模型 Invocation 状态权威；
8. V1 只做 NodeSecret，不做可下发到普通物理 Node 的 Workspace Vault；
9. V1 使用精确 model resource 和可选固定 Deployment，不建设通用调度 DSL；
10. 第二个、第三个真实资源类型出现后，再提炼通用 Asset Registry。

一句话定义：

> Workspace 逻辑拥有共享资源与授权；WorkspaceRegistry 拥有明文定义写权；WorkspaceNode 拥有部署、Secret、本地数据和执行权威；HubService 只提供可选的身份、目录、密文投递与安全连接。

## 3. Account、Workspace 与 HubService

### 3.1 AccountSubject

AccountSubject 回答“你是谁”，负责登录、恢复、订阅和成员身份。稳定身份键必须是：

```text
AccountSubjectKey = identity_issuer_id + subject_id
```

不能只使用裸 `subject_id`，否则 Hosted Hub 与 Self-hosted Hub 上相同字符串会被误认为同一身份。

V1 规则：

- 一个 issuer 内 Account 与 Workspace 是多对多关系；
- 一个 Workspace 至少有一个 active owner；
- owner transfer 是原子 membership 操作；
- App 可以保存多个 issuer 的独立登录，但不自动联邦它们；
- service account 仍使用 Membership，不创建第二套身份模型。

### 3.2 Workspace

Workspace 是唯一逻辑租户，拥有：

- Workspace ID 与名称；
- Membership 和 owner 不变量；
- 共享资源的逻辑 ID、Revision digest 和授权关系；
- WorkspaceNode enrollment；
- ResourceGrant；
- deployment intent 和非敏感目录 metadata；
- 审计与配置发布记录。

普通用户首次使用时自动创建 Personal Workspace。产品体验可以仍然是“一账户一个个人中心”，但
底层不把资源外键直接指向 Account。

```text
Account A ── owner  ──> Personal Workspace A
          ├─ member ──> Family Workspace
          └─ member ──> Work Workspace
```

### 3.3 HubService

HubService 是可选基础设施部署，可以是 Knoa Hosted 或 Self-hosted。一个 HubService 可以承载多个
Workspace，但 Workspace 不等于 HubService。

HubService V1 负责：

- Account authentication 与 Workspace Membership；
- Workspace/WorkspaceNode Directory；
- Node presence 和 DeploymentObservation；
- WorkspaceNode enrollment；
- opaque rollout envelope；
- Invocation Ticket 签发和 issuance rate limit；
- direct connection coordination 和 Relay；
- 非权威的 invocation audit observation。

HubService V1 不负责：

- 保存 Agent/Profile/Prompt/Skill/MCP 的明文定义；
- 修改 WorkspaceNode SQLite 或 live Runtime；
- 保存 NodeSecret；
- 校验 Node-local Principal；
- 成为远程模型执行状态权威；
- 自动合并多个 Node 的配置；
- 代理和解密模型 Prompt 或响应。

### 3.4 No-Hub

No-Hub 模式不是没有 Workspace，而是由本地 owner Node 同时承担：

```text
LocalIdentityAuthority
WorkspaceRegistry
WorkspaceNode
```

No-Hub V1 支持单 Workspace、单 Node 的全部本地能力。多 Node 远程共享需要一个可以签发 Invocation
Ticket 和协调连接的 HubService，因此不属于 No-Hub V1。

本地 Workspace 后续加入 Hub 时必须执行显式迁移：

```text
export signed workspace manifest
  -> create/import target Workspace
  -> re-enroll WorkspaceNode
  -> republish encrypted candidates
```

禁止隐式合并两个 Workspace Registry，也不把同名资源自动视为同一资源。

## 4. NodeHost 与 WorkspaceNode

### 4.1 NodeHost

NodeHost 是物理机器或 Knoa 安装实例，拥有：

- 安装与升级状态；
- OS、CPU/GPU、存储和网络设备能力；
- host-level service supervisor；
- 本地安全存储能力。

### 4.2 WorkspaceNode

WorkspaceNode 是一个 Workspace 内的逻辑执行租户，拥有：

- WorkspaceNode identity private keys；
- NodeOverlayRevision；
- NodeSecret Store；
- Model/Agent/MCP deployment process；
- Runtime generation 和本地 capability policy；
- Conversation、Task、Approval、Artifact；
- 对远程 Invocation 的最终拒绝权。

V1 保持简单：

```text
1 NodeHost = 1 WorkspaceNode = 当前 Node 安装实例
```

因此 V1 不新增 NodeHost 表，也不实现同进程多 Workspace。文档先区分两个概念，是为了避免未来
把“机器身份”和“租户执行身份”继续混为一谈。

若未来同一物理机服务多个 Workspace，必须创建相互隔离的 WorkspaceNode，各自拥有 identity、
Secret、配置和数据作用域。是否共享底层 GPU/model daemon 需要通过显式 Service Grant，而不是让
多个 Workspace 直接共享一个 Core 数据库或 Secret Store。

## 5. V1 最小资源模型

V1 不实现十种通用 Asset Kind 和统一 Binding 框架，只实现远程本地模型所需对象。

### 5.1 ModelResourceRevision

ModelResourceRevision 表示 Workspace 中稳定、不可变的逻辑模型定义：

```text
resource_id
workspace_id
revision
canonical_digest
display_name
provider_protocol = openai_compatible | llamacpp
model_identity
declared_capabilities
created_by
created_at
```

它不包含：

- API Key；
- Node IP、端口或 URL；
- 本地模型路径；
- PID、GPU allocation 或进程状态；
- 可变健康和容量。

Qwen 3.5 4B 是一个 ModelResourceRevision；Node A 上正在运行的 llama.cpp/OpenAI-compatible 服务
是它的 ModelDeployment。

### 5.2 ModelDeploymentSpec

ModelDeploymentSpec 表示“希望在哪个 WorkspaceNode 提供该模型”：

```text
deployment_id
workspace_id
model_resource_revision_id
target_workspace_node_id
enabled
workspace_grant_policy
desired_revision
```

endpoint、model path、Secret、并发和本地进程参数不进入 Workspace 定义，它们属于目标 Node 的
NodeOverlayRevision。

### 5.3 NodeSecretBinding

V1 SecretBinding 只有一个权威：目标 WorkspaceNode。

```text
deployment_id
secret_requirement_name
node_secret_ref
configured/missing
version
```

Workspace 只知道 Deployment 声明了哪些 `SecretRequirement`，以及目标 Node 回报 configured 或
missing；Workspace 和 Hub 都不能读取 Secret value。

### 5.4 ModelRequirement

Agent RuntimeSpec 在 V1 使用确定性选择：

```yaml
model_requirement:
  resource_id: model/qwen-local
  preferred_deployment_id: model-deploy-node-a   # optional
  fallback_resource_ids: []
```

V1 不实现 latency/cost scoring、deny list、表达式 DSL 或任意 capability query。若存在多个
Deployment，按以下确定顺序解析：

1. 显式 preferred deployment；
2. Workspace 默认 deployment；
3. 配置中固定顺序的 fallback；
4. 无可用项则返回 deterministic unavailable。

### 5.5 ResourceGrant

V1 ResourceGrant 只授权 WorkspaceNode 调用某个 ModelDeployment：

```text
grant_id
workspace_id
caller_workspace_node_id
target_deployment_id
capability = model_inference
max_request_deadline
expires_at
revoked_at
```

不在 Hub 镜像 Node-local Principal。Node B 先用本地 Principal policy 决定某个 Agent Turn 是否允许
使用远程模型；Hub 和 Node A 只授权 caller WorkspaceNode。

### 5.6 DeploymentObservation

DeploymentReport 与 CapabilityOffer 在 V1 合并为一个带 TTL 的观察对象：

```text
deployment_id
workspace_node_id
applied_materialized_digest
health_epoch
health
detected_capabilities
available_capacity
observed_at
expires_at
node_signature
```

只有当前 `applied_materialized_digest + health_epoch` 的 Observation 可以被解析器使用。过期或签名
失败时不创建新 Invocation。

连接地址不进入 DeploymentObservation。LAN/private endpoint discovery 和 Relay route 属于独立的
Connection Resolver，避免向 Hub 目录或其他成员泄漏私网地址。

## 6. 配置唯一权威与热发布

### 6.1 三类配置事实

配置必须按字段所有权拆分：

```text
WorkspaceDefinitionRevision
  V1: ModelResource、ModelDeployment intent、WorkspaceNode Grant

NodeOverlayRevision
  endpoint、local model path、NodeSecretRef、进程参数、并发、local deny

MaterializedConfigCandidate
  对一个明确 WorkspaceNode 的只读组合结果
```

V1 中 Agent/Profile/Skill/MCP 仍属于 NodeOverlayRevision；等其进入共享阶段时，再把对应字段一次性
提升到 WorkspaceDefinitionRevision，并从 NodeOverlay schema 删除。禁止同一个字段同时存在于两边。

### 6.2 AppliedReceipt

WorkspaceNode 应用候选后产生：

```text
workspace_definition_digest
node_overlay_digest
materialized_digest
generation_id
apply_status
applied_at
node_signature
```

Hub 只能展示此回执，不能自行把 desired 标为 applied。

### 6.3 发布流程

#### Hub-assisted

```text
Owner App connects to WorkspaceRegistry Node
  -> edits WorkspaceDefinition Draft
  -> registry validates and creates immutable Revision
  -> registry reads target Node public configuration key
  -> produces one complete materialization candidate per target Node
  -> signs candidate digest + target Node + base digests
  -> seals candidate to target WorkspaceNode
  -> Hub stores opaque envelope and delivery metadata
  -> target Node decrypts and verifies
  -> target Node combines with current NodeOverlayRevision
  -> preflight
  -> generation swap / reload
  -> target Node emits AppliedReceipt
```

Hosted Hub V1 看不到明文 Model/Agent/Profile/Prompt/MCP 配置。

#### No-Hub

本地 WorkspaceRegistry 与 WorkspaceNode 同进程时，不需要 Relay 或 sealed envelope，但仍使用同一
Revision、digest、preflight 和 AppliedReceipt 语义，不能绕过 ConfigurationService 直接修改 live
Runtime。

### 6.4 Workspace-managed 字段只读

MaterializedConfigCandidate 中来自 WorkspaceDefinitionRevision 的字段在 Node 管理 UI 中只读，UI
应显示来源 Revision 和“转到 Workspace 设置修改”。Node 页面只能编辑 NodeOverlayRevision。

这消除了 Workspace Draft 与 Node Draft 对相同字段的双写。

### 6.5 热生效

| 变化 | 所有者 | 生效方式 | 活动 Invocation |
| --- | --- | --- | --- |
| ModelResource/默认 Deployment | Workspace | Resolver snapshot swap | 固定原解析结果 |
| Model endpoint/process 参数 | Node Overlay | 新 provider generation | 已 admission 的调用不迁移 |
| NodeSecret rotation | Node | 重建受影响 generation | 不注入旧进程 |
| ResourceGrant revoke | Workspace + target Node 验证 | 停止新 admission | 既有调用按 TTL/revoke policy 收敛 |
| Agent/Profile | V1 Node Overlay | 新 Agent generation + drain | 当前 Turn 不变化 |

“热生效”表示不重启整个 Knoa 服务，不表示活动 Invocation 可以被无损改写。

## 7. Hub 隐私与明文配置权威

### 7.1 V1 WorkspaceRegistry

V1 每个 Personal Workspace 有一个 `WorkspaceRegistry Node`，通常是最先创建 Workspace 的 owner
Node。它是以下明文内容的唯一写入权威：

- WorkspaceDefinition Draft/Revision；
- canonical document 和 dependency digest；
- 发布历史；
- per-Node materialization source。

其他 Node 只持有与自己相关的 materialized slice，不成为 Workspace Registry 的并列 writer。

WorkspaceRegistry Node 离线时：

- 已应用 Node 继续运行；
- 已签发且未过期的本地配置继续有效；
- 不允许创建新的 Workspace Revision；
- Hub 不能代替 Registry 修改明文配置。

### 7.2 Hub 可见内容

Hosted Hub V1 只能看到：

- Workspace、Membership、WorkspaceNode 和 public keys；
- resource/deployment opaque ID 和用户允许公开的 display metadata；
- Revision/candidate digest；
- sealed candidate；
- DeploymentObservation；
- ticket issuance、连接时间、流量大小和 audit observation。

Hosted Hub V1 默认不能看到：

- Agent/Profile/Prompt/Skill instructions；
- MCP URL、Tool 参数和 Secret requirement 名称；
- Provider endpoint；
- Node local path；
- 模型 Prompt、响应和 Tool schema；
- SecretRef 和 Secret value。

### 7.3 Hosted WorkspaceRegistry

未来可以提供 Hosted WorkspaceRegistry/Virtual Node，但必须显式选择信任模式：

- `hosted_trusted`：Knoa 托管服务可读取明文定义；
- 未来若实现客户端 Workspace key，再增加 `hosted_encrypted`。

V1 不实现 Workspace key recovery、多人 E2E key distribution 或 encrypted server-side search，避免在
尚未验证远程模型价值前建设完整密码学协作平台。

## 8. 远程本地 LLM 调用

### 8.1 授权链

```text
Node B local Principal policy
  ∩ RuntimeSpec ModelRequirement
  ∩ Workspace ResourceGrant(caller Node B -> deployment on Node A)
  ∩ live DeploymentObservation
  ∩ Node A local deployment policy/capacity
= admitted remote model invocation
```

职责分离：

- Node B 校验本地用户、Agent 和 Conversation/Task 权限；
- Hub 校验 Workspace membership、caller WorkspaceNode、ResourceGrant 和 ticket issuance limit；
- Node A 校验 ticket、caller Node identity、Deployment digest、本地 policy 和容量；
- Hub 不校验或存储 Node B 的完整 Principal 权限表。

### 8.2 Invocation Ticket

Ticket 至少绑定：

```text
audience = knoa-resource-invocation-v1
workspace_id
invocation_id
caller_workspace_node_id
target_workspace_node_id
target_deployment_id
target_materialized_digest
capability = model_inference
max_deadline
allowed_transports = [direct, relay]
issued_at / expires_at
nonce
```

Ticket 在 transport open 时不消费，在 Node A 成功 admission 时原子消费。对同一 invocation_id 的
重复 open 不得创建第二次执行。

### 8.3 最小状态机

目标 Node 是执行状态权威：

```text
issued                         # Hub ticket observation
  -> admitted                 # Node A 持久化并返回 admission ack
      -> running
          -> succeeded
          -> failed
          -> cancelled
          -> outcome_unknown
  -> rejected
  -> expired                  # admission 前过期
```

Admission ack 必须包含：

```text
invocation_id
accepted_deployment_id
accepted_materialized_digest
accepted_policy_digest
execution_epoch
admitted_at
node_signature
```

### 8.4 Direct、Relay 与 reconcile

调用流程：

```text
Node B obtains ticket
  -> try secure direct route
  -> if no admission ack, use same invocation_id/ticket through Relay
  -> Node A open_invocation is idempotent
  -> if already admitted, return current state and attach token
  -> if not admitted, perform one atomic admission
```

规则：

- direct/Relay 切换只能改变 transport，不能改变 invocation identity；
- admission ack 丢失时，Relay 重连执行 reconcile/attach，不重新推理；
- 首 token 后不得切换 Deployment 或拼接两个模型结果；
- reconnect 使用 `invocation_id + execution_epoch + event_cursor` attach；
- cancel 请求先进入 `cancel_requested` 观察状态，只有 Node A terminal ack 后才显示 cancelled；
- 无法确认是否执行时显示 outcome unknown，不盲目重放；
- Node A 持久 terminal state 至少覆盖 ticket 最大寿命和 attach/reconcile 窗口。

### 8.5 端到端加密

Node B 和 Node A 使用长期 Ed25519 identity key 签署临时 X25519 key 和完整 invocation transcript，
通过 HKDF-SHA-256 派生双向 session key，payload 使用 AEAD 和递增 sequence。

禁止把 Node 的 configuration X25519 private key复用为远程调用 session key。configuration key 只用于
sealed config candidate；远程调用必须使用临时 session key。

Relay 只看到 WorkspaceNode、session、stream、sequence 和 ciphertext size，不读取 Prompt、响应或
Tool schema。

### 8.6 配额与容量

V1 不实现精确分布式 quota reservation：

- Hub 只限制 ticket issuance rate、最大 deadline 和声明预算；
- Node A 是实际并发、token、内存和 deadline 权威；
- Node A 返回签名 usage summary；
- Hub 异步汇总，不做精确计费；
- 真正收费需求出现后再增加 reservation lease 和 reconciliation ledger。

## 9. Secret

### 9.1 V1 仅 NodeSecret

云 Provider API Key、本地 MCP credential 和其他 Secret 继续保存在目标 WorkspaceNode 的 write-only
Secret Store。Workspace Definition 只能声明 SecretRequirement。

UI 只显示：

- requirement name；
- configured/missing；
- owner WorkspaceNode；
- 最后轮换时间；
- 受影响 Deployment。

Secret value 不进入 Workspace Revision、sealed candidate、Hub audit 或普通配置 diff。

### 9.2 Workspace Vault 延后

普通物理 Node 调用云 Provider 时，Secret 必然进入该 Node 的进程内存。没有 enclave/attestation 时，
不能宣称 Vault 可以让执行 Node 无法看到 Secret。

因此 Workspace Vault 只在 Virtual Node/controlled egress service 阶段设计：

- Secret 不下发给普通物理 Node；
- 由 Vault 运营方控制的执行环境完成云 API 调用；
- consumer Node 只获得模型响应；
- trust、地区、日志和数据保留必须显式展示。

## 10. Package、Agent、Skill、MCP 与 Tool 的后续抽象

V1 不建立通用 Asset/Binding 表，但保留未来一致模式：

```text
Stable Resource ID
  -> immutable Resource Revision
  -> dependency lock/digest
  -> target-specific Deployment/Materialization
  -> Node-local Secret/endpoint/process
```

### 10.1 Agent

Agent 进入 Workspace 共享时，首个共享单元应是 `AgentPackageRevision`，固定：

- AgentDefinition；
- AgentProfile；
- RuntimeSpec；
- Prompt/Skill dependency revision digests；
- ModelRequirement；
- policy ceiling。

不把 AgentDefinition、Profile、RuntimeSpec 分别做成可独立漂移的 Alias 图。只有出现真实的独立复用
需求后再拆分。

### 10.2 Skill

Skill 继续是 data-only immutable package。Workspace 可用不等于所有 Node 已下载，也不等于 Agent
获得授权。Node 按 digest 拉取、验证和缓存。

### 10.3 MCP 与 Tool

MCP Definition 与 Node Deployment 分离；第三方 Tool 仍只通过 MCP 和 Capability Gateway 进入平台。
MCP inventory drift 继续 fail closed。网络下载内容不能注册进程内 Tool implementation。

### 10.4 何时提取通用 ResourceRevision

只有 ModelResource、AgentPackage 和 MCPPackage 至少两个领域出现稳定的相同查询与发布流程后，才从
真实消费者提取通用 `ResourceRevision` 基础结构。禁止 Phase 0 先建设十种空 Asset Kind、通用
Binding service 或任意 selector DSL。

## 11. Workspace 内与跨 Workspace 共享

### 11.1 V1 Workspace 内共享

V1 是 Personal Workspace，只有 owner 和多个 WorkspaceNode。共享通过 ResourceGrant 表达，不增加
`selected_members/selected_nodes` visibility 枚举。

发现可见性第一阶段只有：

- `private`：仅 owner 管理；
- `workspace`：Workspace 成员可发现。

是否可以调用、部署或管理由独立 Grant 决定，不能把可见性当授权。

### 11.2 跨 Workspace

跨 Workspace 保留两个正确概念，但不进入 V1 表、API 或 UI：

- `Package Share`：分享不可变定义，消费者固定 digest 并建立自己的 Secret/Deployment；
- `Service Grant`：分享受限调用能力，不分享机器、Secret、模型文件或 Deployment 管理权。

它们在远程模型同 Workspace 调用稳定后另立设计，不提前创建空 `resource_share` 表、计费字段或公开
Marketplace。

## 12. Virtual Node

Virtual Node 继续复用 WorkspaceNode invocation、deployment、Secret 和 observation 合同，但不进入
V1。

逻辑合同相同不代表信任等级相同。未来必须展示：

- operator；
- region；
- tenant isolation；
- Secret custody；
- data retention；
- supported Runtime/Provider；
- cost policy。

第一版 Hosted Virtual Node 只应是固定规格、少量可信 driver 的受控 worker，不建设通用容器调度器。

## 13. App 信息架构

### 13.1 V1 页面

```text
Workspace Header / Switcher
├── Nodes
├── Models
│   ├── Model Resource
│   └── Deployment Detail
└── Configuration History

Node Detail
├── Model Deployments
├── Local Endpoint / Process
├── Local Secrets
├── Health / Capacity
└── Applied Receipt
```

V1 模型向导：

```text
添加本地模型
  -> 选择目标 Node
  -> 选择 openai-compatible / llama.cpp
  -> Node 页面配置 endpoint/model path
  -> connection test
  -> 创建 ModelResourceRevision
  -> 创建 ModelDeploymentSpec
  -> 设置允许调用的 WorkspaceNode
  -> publish / preflight / apply
```

Node A 模型详情必须明确显示：

- 模型文件和进程只在 Node A；
- 哪些 WorkspaceNode 可以远程调用；
- direct/Relay 当前路径；
- capacity、health、最近调用；
- NodeSecret configured/missing。

### 13.2 后续页面

Agent、Skill、MCP、Sharing、Workspace Vault、Activity/Usage 和 Virtual Node 页面只在对应阶段实现，
不在 V1 放置空入口。

## 14. V1 最小数据模型

```text
workspace(
  id, identity_authority_id, name, kind, status, created_at
)

workspace_membership(
  workspace_id, identity_issuer_id, subject_id,
  role, status, created_at
)

workspace_node(
  id, workspace_id, display_name, identity_public_keys,
  registry_role, platform, version, status, last_seen_at
)

model_resource_revision(
  id, workspace_id, resource_id, revision, canonical_digest,
  display_metadata, provider_protocol, model_identity,
  declared_capabilities, created_by, created_at
)

model_deployment_spec(
  id, workspace_id, model_resource_revision_id,
  target_workspace_node_id, desired_revision,
  enabled, created_at
)

resource_grant(
  id, workspace_id, caller_workspace_node_id,
  target_deployment_id, capability,
  max_request_deadline, expires_at, revoked_at
)

deployment_observation(
  deployment_id, workspace_node_id,
  applied_materialized_digest, health_epoch,
  health, capabilities, available_capacity,
  observed_at, expires_at, node_signature
)

rollout_envelope(
  rollout_id, target_workspace_node_id,
  workspace_definition_digest, node_overlay_base_digest,
  candidate_digest, sealed_candidate,
  state, expires_at, updated_at
)

applied_receipt_observation(
  target_workspace_node_id, workspace_definition_digest,
  node_overlay_digest, materialized_digest,
  generation_id, reported_status,
  report_seq, node_signature, observed_at
)

invocation_ticket_audit(
  ticket_id, invocation_id, workspace_id,
  caller_workspace_node_id, target_deployment_id,
  issued_at, expires_at, issuance_state
)

invocation_audit_observation(
  invocation_id, reporting_workspace_node_id,
  reported_state, execution_epoch, report_seq,
  usage_summary, node_signature, observed_at
)
```

NodeSecret、NodeOverlayRevision 和 authoritative Invocation state 只存在于对应 WorkspaceNode，不进入
Hub 数据库。

## 15. V1 API 与协议

### 15.1 Workspace/Hub Control API

```text
GET        /v1/workspaces
GET        /v1/workspaces/{id}
GET        /v1/workspaces/{id}/nodes
GET        /v1/workspaces/{id}/models
GET        /v1/workspaces/{id}/model-deployments
GET/POST   /v1/workspaces/{id}/resource-grants
GET        /v1/workspaces/{id}/deployment-observations
POST       /v1/resource-invocation-tickets
GET        /v1/resource-invocations/{id}/observations
```

明文 Workspace Revision CRUD 终止在 WorkspaceRegistry Node，不经过 opaque Hosted Hub。

### 15.2 WorkspaceRegistry Node API

```text
GET/POST   /v1/workspace-registry/model-resources
GET/POST   /v1/workspace-registry/model-resources/{id}/revisions
GET/POST   /v1/workspace-registry/model-deployments
POST       /v1/workspace-registry/publish
GET        /v1/workspace-registry/history
```

### 15.3 WorkspaceNode Deployment Contract

```text
fetch_rollout_envelope
decrypt_and_verify_candidate
preview_materialization
preflight_materialization
apply_materialization
report_applied_receipt
publish_deployment_observation
```

### 15.4 Remote Model Contract

```text
open_invocation(ticket, caller_proof, request_metadata)
admission_ack
stream_model_input
stream_model_output
attach_invocation(invocation_id, execution_epoch, cursor)
cancel_invocation
reconcile_invocation
terminal_state
```

模型 payload 使用类型化 schema；不把整个 Core HTTP API 暴露成 Node-to-Node 通用代理。

## 16. 模块边界

```text
HubService
├── identity              Account / Workspace / Membership
├── workspace_directory   WorkspaceNode / public identity / presence
├── rollout_store         opaque sealed envelope
├── resource_directory    DeploymentObservation
├── grant_service         WorkspaceNode ResourceGrant
├── ticket_service        Invocation Ticket / issuance limit
├── relay                 opaque stream transport
└── audit_observation     non-authoritative receipts / usage

WorkspaceRegistry Node
├── workspace_registry    plaintext ModelResource Revision
├── materializer          per-Node immutable candidate
└── publish_service       signing / sealing / history

WorkspaceNode
├── node_overlay          local config revision
├── deployment_manager    model process / generation
├── model_endpoint        inbound remote inference
├── model_invoker         outbound remote inference
├── invocation_store      authoritative state / attach / reconcile
├── secret_store          NodeSecret
├── agent_runtime         local Agent execution
└── secure_connector      direct / Relay E2E session
```

禁止依赖：

- HubService 不导入 Agent/LLM/MCP Runtime implementation；
- WorkspaceRegistry 不读取目标 Node Secret 或 live process；
- Node model endpoint 不读取 Hub 数据库，只验证 ticket 和本地 policy；
- model invoker 不绕过本地 Agent/Principal policy；
- relay 不理解 model payload；
- App 不直接编辑数据库、配置文件或 sealed candidate。

## 17. 分阶段实施

### Phase 0：术语和最小身份收敛

- 将现有逻辑 Hub tenant 明确迁移为 Workspace；
- Hub 改称 HubService/control-plane deployment；
- Account identity 增加 issuer scope；
- 当前 Node 作为一个 WorkspaceNode；
- V1 不创建 NodeHost 表、不做身份 federation。

### Phase 1：单模型资源与部署观察

- ModelResourceRevision；
- ModelDeploymentSpec；
- NodeOverlay 中的 endpoint、path、Secret 和 capacity；
- DeploymentObservation；
- App Workspace Header、Nodes、Models、Deployment Detail。

### Phase 2：Node A Qwen -> Node B Agent

- WorkspaceNode ResourceGrant；
- Invocation Ticket；
- Node-to-Node E2E direct/Relay transport；
- admission、attach、reconcile、cancel 和 terminal state；
- Node A authoritative usage/health；
- 不做精确计费和通用 resolver。

### Phase 3：配置字段分区与密文发布

- WorkspaceDefinitionRevision；
- NodeOverlayRevision；
- per-Node MaterializedConfigCandidate；
- Workspace-managed 字段 Node 只读；
- AppliedReceipt；
- 保持 Hosted Hub opaque。

### Phase 4：第二类资源验证通用抽象

- 优先 AgentPackageRevision 或 MCPPackageRevision；
- 固定 dependency closure digest；
- 从两个真实消费者提取最小 ResourceRevision；
- 不做通用 Binding service 或 selector DSL。

### Phase 5：Virtual Node 与 Vault

- controlled egress/worker；
- Workspace Vault 只服务受控执行环境；
- region、operator、trust、retention 和 cost 展示；
- 仍使用同一 WorkspaceNode remote invocation contract。

### Phase 6：跨 Workspace

- Package Share；
- 私有 Service Grant；
- 双边审计和撤销；
- 出现真实商业需求后再设计计费和公开 Marketplace。

## 18. KISS / YAGNI 边界

V1 实现：

- Personal Workspace；
- HubService 可选；
- 一个 NodeHost 对应一个 WorkspaceNode；
- ModelResourceRevision；
- ModelDeploymentSpec；
- NodeSecretBinding；
- 精确 ModelRequirement；
- WorkspaceNode ResourceGrant；
- DeploymentObservation；
- 幂等远程模型 Invocation；
- direct 优先、Relay fallback；
- Workspace/Node 配置字段分区。

V1 不实现：

- 通用 Asset/Binding 框架；
- 多条件调度 DSL；
- 多 Workspace 单进程 Node；
- Workspace Vault；
- Virtual Node；
- 精确分布式计费 quota；
- 跨 Hub 身份联邦；
- 跨 Workspace 分享；
- 公开市场；
- 通用容器编排；
- Hub 明文配置数据库。

## 19. 验收不变量

1. Workspace 是唯一逻辑租户；HubService 不是第二个资源租户；
2. AccountSubject identity 必须带 issuer；
3. No-Hub Personal Workspace 有明确的本地 Registry 权威；
4. Hosted Hub V1 不能读取明文 Workspace 配置；
5. 同一配置字段只能属于 WorkspaceDefinition 或 NodeOverlay 之一；
6. Workspace-managed 字段在 Node 上只读；
7. Hub 不能把 desired 或 observation 冒充 Node applied/terminal state；
8. V1 NodeSecret 的唯一所有者是目标 WorkspaceNode；
9. Agent 模型需求不包含 IP、端口、API Key 或本地路径；
10. ModelDeployment 必须绑定明确 WorkspaceNode 和 materialized digest；
11. DeploymentObservation 过期、签名失败或 digest 不匹配时不能创建新 Invocation；
12. Hub 不校验 Node-local Principal；
13. 同一 invocation_id 最多执行一次，重复 open 只能 attach/reconcile；
14. admission 后切换 direct/Relay 不得重启推理；
15. 首 token 后不得切换 Deployment 或拼接结果；
16. cancel 未获 terminal ack 时不得显示已取消；
17. outcome unknown 时不得盲目重放；
18. Hub 只做 issuance limit，Node 是实际 capacity/usage 权威；
19. configuration key 不得复用为 invocation session key；
20. Node A 的 Qwen 3.5 4B 可以被获权的 Node B Agent 通过 E2E session 调用。

## 20. 最终架构定义

```text
身份属于 issuer-scoped AccountSubject
租户和共享授权属于 Workspace
明文共享定义属于 WorkspaceRegistry
本地配置、Secret、数据和执行属于 WorkspaceNode
身份目录、密文投递和 Relay 由可选 HubService 提供
远程 Invocation 终态属于执行 Node
```

本地 Qwen 3.5 4B 不是某个 Agent 私有的 endpoint 配置，而是 Workspace 中的 ModelResourceRevision
在 Node A 上的一个 ModelDeployment。Node B 的 Agent 只声明精确 ModelRequirement；HubService 签发
有界 ticket 并协调连接；Node A 完成 admission、推理、流式输出、cancel、reconcile 和终态持久化。

这是当前最小且正向的资源共享架构。它验证成功后，再用真实的 AgentPackage/MCPPackage 消费者
提炼通用 ResourceRevision，而不是先建设一套没有消费者的资源平台。
