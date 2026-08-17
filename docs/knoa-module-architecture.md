# Knoa 完整模块架构

> 状态：目标架构与当前实现映射（未实现能力会明确标记）
>
> 更新日期：2026-08-17
>
> 权威顺序：产品语义以 `knoa-product-domain-architecture.md` 为准；实现事实以运行代码与测试为准；模块边界以本文为准；专题设计文档补充细节
>
> 设计取向：正向设计；高内聚、低耦合；KISS、YAGNI；不保留旧配置模型兼容层

## 1. 文档定位

本文是 Knoa 当前模块架构的统一入口，回答四个问题：

1. 系统由哪些模块组成；
2. 每个模块拥有什么状态和职责；
3. 模块之间允许怎样依赖和调用；
4. Agent、模型、Skill、MCP、配置热发布和移动 App 如何连成一条完整链路。

本文不重复所有专题细节。以下文档仍是对应领域的详细设计：

- `docs/knoa-product-domain-architecture.md`：Account、Workspace、Node、资源、配置入口和生命周期的产品权威；
- `docs/architecture.md`：Conversation、Task、Capability、Approval 等平台概念概览；
- `docs/knoa-agent-profile-delegation-design.md`：Agent Runtime、Profile、Invocation Policy 与 Subagent；
- `docs/knoa-configuration-control-plane-design.md`：配置 Registry、管理页面、发布与热生效；
- `docs/knoa-secure-gateway-design.md`：设备、认证和远程接入安全；
- `docs/knoa-extension-model-hub-node-design.md`：扩展生态、模型中心、Account、HubService、Relay 与多节点；
- `docs/knoa-workspace-resource-fabric-design.md`：Workspace 资源归属、ModelDeployment、DeploymentObservation、跨 Node 模型调用与配置权威；
- `docs/knoa-deployment-architecture.md`：Node、HubService、Relay、App、LLM 与 MCP 的进程、网络、安全和运维部署拓扑；
- `docs/knoa-durable-task-design.md`：持久 Task 执行与恢复语义；
- `docs/knoa-capability-extension-design.md` 与
  `docs/knoa-standard-mcp-host-design.md`：Skill/MCP 扩展与标准 MCP Host。

## 2. 架构原则

### 2.1 单一状态所有者

每类状态只有一个模块拥有写入权：

| 状态 | 唯一写入边界 |
| --- | --- |
| Workspace Conversation 目录投影 | `WorkspaceRegistry` |
| Conversation 正文 / ChatTurn 生命周期 | 绑定 Node 的 `ConversationService` |
| Workspace Task Definition / Deployment | `WorkspaceRegistry` / Workspace control plane |
| TaskExecution / Attempt 生命周期 | 目标 Node 的 `TaskService` |
| AgentInvocation / ExecutionAttempt placement 与运行事实 | 执行 `WorkspaceNode` |
| Approval / HumanInteraction | 对应 Core service |
| Agent Session binding | `AgentExecutionService` / binding repository |
| Invocation policy snapshot | Agent policy repository |
| Delegation relationship | `DelegationService` |
| Workspace Agent/Model/Skill/MCP 定义与 Published Spec | `WorkspaceRegistry` |
| WorkspaceNode enrollment 与 Node config Desired State | Workspace control plane |
| Resource/Task Deployment intent | Workspace control plane 的独立边对象 |
| NodeOverlay、Node Secret 与 materialized runtime configuration | 目标 `WorkspaceNode` 的 `ConfigurationService` |
| Node hardware/runtime Observation | 目标 `WorkspaceNode` |
| Tool authorization and execution | `CapabilityGateway` |
| Artifact 目录 metadata/reference | Workspace projection |
| Artifact 权威 metadata and bytes | 产生 Artifact 的 Node `ArtifactStore` |
| Node signing/configuration identity | `NodeIdentityStore` |
| Immutable extension package bytes | `PackageStore` |
| Provider credentials | Node-local `SecretStore` |
| Account、Workspace membership、Node directory、presence、ticket | optional `HubService`；No-Hub 由 owner Node 承担本地身份/Registry |
| Relay connection/frame forwarding | `RelayBroker` |

Channel、App、Agent Runtime、Skill 和 MCP Server 都不能绕过这些边界直接修改平台状态。

### 2.2 组合优于类型爆炸

Agent 使用组合模型：

```text
RuntimeSpec + AgentProfile = AgentDefinition
```

专业角色不是新的 Runtime 类型；Subagent 也不是新的 Runtime 类型。它们复用统一执行、
权限、Task、Artifact、Approval 和审计设施。

### 2.3 权限只能收窄

一次调用的最终权限是多层约束的交集：

```text
Principal grant
  ∩ RuntimeSpec ceiling
  ∩ AgentProfile ceiling
  ∩ invocation/delegation request
  = ResolvedInvocationPolicy
```

Profile、Skill、Agent 和 Reviewer 都不能创造权限。

### 2.4 控制面与执行面分离

- 控制面负责配置编辑、校验、预检、发布和 desired/applied generation 状态；
- 执行面只消费已发布配置和本次不可变 policy snapshot；
- 配置发布不原地修改正在执行的 Runtime 对象。

### 2.5 少量可信扩展点

当前只保留确有第二实现的抽象：

- `AgentRuntime` SPI：Native 与 Codex；
- Tool Registry：Built-in、Platform、MCP proxy；
- Channel adapter：Mobile/Gateway、CLI/TUI、Feishu、Webhook；
- Extension provider：Skill 与 MCP。

不引入通用插件内核、独立 Policy Engine、消息总线、Agent swarm 或可下载 Runtime class。

## 3. 总体分层

```text
┌──────────────────────────── Clients / Channels ────────────────────────────┐
│ Mobile App │ CLI │ Textual TUI │ Feishu │ Webhook                         │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ authenticated typed protocol
                                    v
┌──────────────────────── Secure Gateway / CoreClient ───────────────────────┐
│ pairing │ device identity │ session auth │ rate limit │ API │ SSE/events  │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    v
┌──────────────────────────────── Core ──────────────────────────────────────┐
│ Conversation │ Task │ Automation │ Artifact │ Interaction                 │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    v
┌────────────────────────── Agent Orchestration ─────────────────────────────┐
│ DefinitionResolver │ ExecutionService │ AgentManager │ DelegationService  │
│ SessionBinding     │ InvocationPolicy snapshots │ System Agent ports     │
└───────────────────┬─────────────────────────────┬───────────────────────────┘
                    v                             v
┌──────── Unified Agent Runtime SPI ───────┐  ┌──────── Capability Gateway ───────┐
│ Native Runtime        │ Codex Adapter   │  │ grant │ schema │ policy │ ToolStep │
└──────────────────────┬───────────────────┘  └──────────────┬───────────────────┘
                       └──── granted MCP calls ─────────────>│
                                                            v
                                              Built-in Tools / MCP Tools
                                                            │
                                                            v
                                                   Host / External Systems

Configuration Control Plane 位于上述执行链侧面，通过一次发布屏障替换 Resolver、Runtime
generation 和 Extension snapshot；它不是 Conversation/Task 的普通产品领域。
```

依赖方向始终从外层适配器指向内层应用服务和领域合同。Core 不依赖 Mobile、Feishu 或某个
具体 MCP Server；Agent Runtime 不拥有 Platform repository；外部系统也不能反向驱动
Platform 状态机。

## 4. 部署与进程拓扑

本节只描述模块到进程的映射；完整的部署形态、Node–Hub Edge 定义、端口/TLS、持久化、备份、
故障语义和生产化缺口以 `docs/knoa-deployment-architecture.md` 为准。

当前服务由 `ApplicationDaemon` 统一拥有生命周期：

```text
ApplicationDaemon
├── CoreDaemon
│   ├── CoreRuntimeComposition
│   ├── CoreHost / CoreServer
│   ├── ConversationService
│   ├── TaskService
│   ├── ScheduleDispatcher / TriggerDispatcher
│   ├── MCP Resource Task bridge
│   ├── Capability MCP Host
│   └── ExtensionManager
├── SecureGatewayAdapter        # 配置启用时
├── WebhookAdapter              # 配置启用时
└── ChannelRuntime
    └── FeishuChannel           # 配置启用时
```

这是生命周期组合，不是权限合并。Gateway、Webhook 和 Channel 仍通过 CoreClient/Core API
访问 Core，不能直接持有 Core repository。

Agent Runtime 的部署有两种：

- Native Runtime 在 Platform 进程内执行，由 Platform 管理模型连接；
- Codex Runtime 通过受信 adapter 连接外部 Codex App Server/进程，由 Codex 自己管理
  模型、认证和模型选择。

Self-hosted Hub 是独立可选进程，不进入 `ApplicationDaemon`，也不持有 Core repository：

```text
knoa-hub
├── HubService / HubRepository
├── Account bootstrap boundary
├── Node directory / enrollment / presence / tickets
├── opaque Fleet envelopes
└── RelayBroker
```

形态 3 单节点 Hosted 复用同一个 `knoa-hub` 入口，但使用独立 Hosted composition：

```text
knoa-hub --deployment-mode hosted_single_node
├── HostedControlRepository
│   ├── Account / LoginIdentity / PasswordCredential / Session
│   └── Workspace / Membership / one-time enrollment and reset grants
├── shared Hub signing identity
├── Hosted Android Release Repository
└── HostedTenantDispatcher
    └── isolated HubApplication per Workspace
        ├── HubRepository
        └── RelayBroker
```

它是个人和受控小规模使用的单节点 Hosted MVP：帐号控制面位于 `control.db`，每个 Workspace 业务
状态位于独立 tenant `hub.db`；Hosted Android APK 位于根级共享 release repository，不归属某个
Workspace 或 Node。控制库、tenant DB、release repository 与 signing key 形成一个恢复单元。它不把
tenant 包装成独立物理进程，也不改变 Self-hosted 或 No-Hub composition。

Node 侧的 `Node Hub Edge Adapter`（当前由 `NodeHubService + NodeRelayManager` 构成）保存单 Hub
enrollment，并从 Secure Gateway 生命周期
启动 outbound Relay connector；App 侧 `GatewayTransport` 统一 direct fetch 与 Relay encrypted
transport。Relay ciphertext 内承载现有 Gateway HTTP typed contract，Node 通过 ASGI 调用同一个
`SecureGatewayAdapter.app`，因此不存在第二套业务控制器或 Relay 专用 Core API。

## 5. 源码模块地图

### 5.1 Platform 服务与组合

| 模块 | 主要职责 | 主要位置 |
| --- | --- | --- |
| Composition | 构造依赖、启动当前配置、应用新配置 | `src/knoa_platform/agent_runtime/composition.py` |
| Service lifecycle | Core/Application/Channel 生命周期 | `src/knoa_platform/service/` |
| Core API | typed command/query 边界 | `src/knoa_platform/service/core_api.py`、`core_*_commands.py` |
| Secure Gateway | 认证、路由、协议、流式事件、发布管理 | `src/knoa_platform/gateway/` |
| Mobile Release Domain | APK 校验、不可变 manifest/repository 与共享 wire payload | `src/knoa_platform/mobile_releases.py` |
| Hosted Hub | Account/Workspace/Relay 与 Hosted Android release HTTP/管理边界 | `src/knoa_platform/hub/` |
| CLI/TUI | 本地管理与交互适配 | `src/knoa_platform/cli_*.py`、`src/knoa_platform/ui/` |
| Channels | 飞书等 Channel adapter | `src/knoa_platform/channels/` |

### 5.2 Agent 与执行

| 模块 | 主要职责 | 主要位置 |
| --- | --- | --- |
| Agent definitions | RuntimeSpec/Profile/Definition/Policy 类型与解析 | `src/knoa_platform/agents/definitions.py` |
| Agent manager | active generation、并发 lease、health、swap/drain | `src/knoa_platform/agents/manager.py` |
| Agent execution | 唯一 Runtime 调用入口、binding、grant、event stream | `src/knoa_platform/agents/execution.py` |
| Session binding | Product Session 到 Runtime Session 的不透明绑定 | `src/knoa_platform/agents/bindings.py` |
| Policy snapshots | 持久化本次不可变授权 | `src/knoa_platform/agents/policies.py` |
| Delegation | Child Task、父子关系、预算和结果治理 | `src/knoa_platform/agents/delegation.py` |
| Runtime SPI contracts | 对外 Runtime Session/Turn/event/interaction 合同 | `src/knoa_agent_contracts/` |
| Platform runtime DTO | Core 内部 Scope、Artifact attachment、健康与工具 DTO | `src/knoa_platform/agent_runtime/contracts.py` |
| Native runtime | Knoa 原生 Agent loop 与模型/工具步骤 | `src/knoa_agent/`、`src/knoa_platform/agent_runtime/` |
| Codex adapter | Codex runtime integration | `src/knoa_codex_agent/` |

### 5.3 产品领域

| 模块 | 主要职责 | 主要位置 |
| --- | --- | --- |
| Conversation | 会话、Turn、进度、重试和历史 | `src/knoa_platform/conversation/` |
| Task | Definition、Execution、Attempt、ToolStep、Approval | `src/knoa_platform/tasks/` |
| Automation | Schedule、Trigger 和事件投递 | `src/knoa_platform/automation/` |
| Artifact | 文件注册、交付、Tool output 收敛 | `src/knoa_platform/artifacts/` |
| Context/Memory | 上下文组装、压缩、记忆和 session context | `src/knoa_platform/context/` |
| Interaction | 等待用户输入与恢复 | `src/knoa_platform/interactions.py` |
| Observation | trace 与 benchmark | `src/knoa_platform/observability/`、`benchmark/` |

### 5.4 能力与扩展

| 模块 | 主要职责 | 主要位置 |
| --- | --- | --- |
| Capability Gateway | Tool 授权与副作用安全边界 | `src/knoa_platform/capabilities/gateway.py` |
| Tool Registry | Built-in 与 Platform Tool 注册 | `src/knoa_platform/tools/` |
| Skill provider | data-only Skill 装载与验证 | `src/knoa_platform/extensions/skill.py` |
| MCP provider | MCP lifecycle、tool/resource/prompt adapter | `src/knoa_platform/extensions/mcp.py` |
| MCP onboarding | MCP 配置接入与 secret 引用 | `src/knoa_platform/extensions/mcp_onboarding.py` |
| MCP automation bridge | Resource event 到 Task launch | `src/knoa_platform/extensions/mcp_resource_tasks.py` |
| Extension import | staging、inspect、provenance、只创建 Config Draft | `src/knoa_platform/extensions/import_service.py` |
| Package store | Skill/MCP immutable content-addressed bytes | `src/knoa_platform/extensions/package_store.py` |
| Provider secrets | write-only Node-local credential storage | `src/knoa_platform/secrets.py` |

### 5.5 配置控制面与 App

| 模块 | 主要职责 | 主要位置 |
| --- | --- | --- |
| Config models | ManagedConfig、Draft、Revision、ControlState | `src/knoa_platform/configuration/models.py` |
| Config registry | SQLite revision/draft/state 持久化 | `src/knoa_platform/configuration/repository.py` |
| Config service | 唯一写入口、validate/preflight/publish；现有 rollback 为待移除旧接口 | `src/knoa_platform/configuration/service.py` |
| Config API | Core typed commands 与 owner-only Gateway routes | `src/knoa_platform/service/core_configuration_commands.py`、`gateway/routes/configuration.py` |
| Mobile App | Chat、Task、Approval、Artifact、配置与发布 | `apps/knoa-mobile/` |
| Node identity | 用途隔离的 Ed25519/X25519 Node keys | `src/knoa_platform/node_identity.py` |
| Fleet candidate | sealed candidate 校验与 Node-local publish | `src/knoa_platform/fleet.py` |
| Self-hosted Hub composition | 单 Workspace Account、directory、ticket、opaque Relay | `src/knoa_platform/hub/app.py`、`service.py`、`repository.py` |
| Hosted Hub single-node composition | Hosted Account/Session、Workspace/Membership、隔离 tenant Hub/Relay | `src/knoa_platform/hub/hosted.py` |
| Hosted Hub administration | 一次性注册/恢复 QR、本地 Node enrollment、一致性备份/恢复 | `src/knoa_platform/hub/admin.py` |
| Node Hub Edge Adapter | Hub enrollment、identity pin、outbound connector、E2E tunnel dispatch | `src/knoa_platform/node_hub.py`、`relay_protocol.py` |
| App transport | direct 优先、Relay fallback、Node session crypto、有限事件轮询 | `apps/knoa-mobile/src/api/gatewayTransport*.ts`、`relayCrypto.ts` |

Mobile App 是账户级移动控制台，不是单 Node 的远程终端。内部拆分为四个状态边界：

```text
Identity state
  issuer -> Account session

Workspace control state
  memberships -> active Workspace -> Work / resources / grants

Node directory state
  Hub directory + presence <-> local pinned trust bindings

Optional Node execution state
  selected Node? -> direct/Relay transport -> Node authentication -> Invocation/Attempt/live control
```

Identity client 拥有 Hosted Account session；Workspace client 拥有 membership 与 active Workspace；
Node directory projection 聚合 Hub directory/presence 和本机 SecureStore binding，但不混淆两种权威；
`NodeSessionProvider` 只拥有可空的 selected Node 及其执行连接。App Shell 在 Account 登录后立即可用，
不等待 Node。单个 Node 的 offline/error 是局部状态，不能阻塞切换 Workspace、管理帐号、查看目录、
退出当前 Node 或连接其他 Node。No-Hub 模式使用本地 identity + local Workspace 进入相同 Shell，
不维护第二套 UI。

Mobile 的权威详细架构和路由以 `docs/knoa-mobile-app-design.md` 为准。Conversation 目录、Task
Definition/Deployment 与资源都在 Workspace scope；Conversation 必须绑定 Node，已启用 Task 必须部署
到 Node。启动时可以恢复上次 Work 和目标 Node，但必须重建完整 Account/Workspace 父级。Node 是
内容与执行权威，不是 Work Definition 的产品所有者，也不是 Account 或 App 根状态。

## 6. Agent 领域模型

```text
Model Deployment
       │
       v
Runtime Implementation
       │
       v
RuntimeSpec ─────────┐
                     ├──> AgentDefinition ──> stable agent_id
AgentProfile ────────┘             │
                                   v
                         ResolvedInvocationPolicy
                                   │
                                   v
                              Invocation
                                   │
                                   └── optional DelegationLink -> Child Task
```

### 6.1 Runtime Implementation

Runtime Implementation 是 `AgentRuntime` SPI 的代码实现类型，当前可信集合只有：

- `native`：Platform 原生 Runtime；
- `codex`：Codex App Server adapter。

它不是用户可见 Agent 身份，也不包含角色语义。

### 6.2 RuntimeSpec

RuntimeSpec 描述怎样执行：

- implementation；
- 模型所有权和模型绑定；
- command、home、cwd、sandbox、approval policy；
- 并发、超时和事件队列限制；
- Runtime-native capability ceiling；
- Profile instruction 注入能力。

模型绑定属于 RuntimeSpec，不属于 Profile。

### 6.3 AgentProfile

Profile 描述以什么角色和边界执行：

- system/developer instructions；
- Workspace Skill references、默认激活集合与 activation policy；
- Platform Tool 与 capability ceiling；
- Runtime-native capability ceiling；
- 迭代和输出限制；
- delegation policy；
- caller allowlist ceiling。

Profile 是可信、版本化数据，不含模型 endpoint、API key 或 Runtime command。

### 6.4 AgentDefinition

AgentDefinition 将一个 RuntimeSpec 和一个 Profile 组合为稳定 Agent 身份：

```text
AgentDefinition
├── agent_id
├── runtime_spec_id
├── profile_id
├── enabled
├── visibility
└── config digest
```

同一种 Runtime implementation 可以承载多个专业 Agent；同一个 Profile 也可以在满足
instruction/capability 要求的不同 RuntimeSpec 上复用。

AgentDefinition 是唯一组合根，不得覆盖 RuntimeSpec 的模型/sandbox/command/并发，也不得覆盖 Profile
的 instructions/Skill refs/Tool/delegation ceiling。Profile 不拥有 Skill package 或 Skill instance；
`visibility` 只控制 Agent 在产品目录中的发现范围；
真正的调用资格仍由 Profile caller ceiling、principal policy 和 ResolvedInvocationPolicy 共同收窄。

### 6.5 Invocation 与 policy snapshot

AgentDefinition 不是一次运行。每次 Conversation Turn、Task Attempt 或 System 调用都会
解析出一个 `ResolvedInvocationPolicy`，并随 Turn/Task 持久化。它固化：

- definition/runtime/profile digest；
- user、delegate 或 system 调用类型；
- caller；
- Platform capability 和 Tool allowlist；
- Skill allowlist；
- Runtime-native capability；
- Artifact scope；
- deadline、Tool、Artifact 和 Child 限制；
- config revision。

执行、重试、Skill activation 和 Capability Gateway 消费同一份快照，避免各模块重新解释
Profile。紧急撤权 registry 目前属于规划能力；当前依靠 Turn cancellation、grant TTL/revoke 和
配置发布后的新调用收窄，不把尚未实现的动态撤权描述为现有保证。

## 7. 当前 Agent 实例映射

| Agent | RuntimeSpec | Profile | 模型所有权 | 当前角色 |
| --- | --- | --- | --- | --- |
| `knoa` | `native-main` | `assistant` | Platform-managed | 默认通用助手 |
| `reviewer_agent` | `native-approval-reviewer` | `approval-reviewer` | Platform-managed | Platform 自动审批建议 |
| `codex` | `codex-default` | `coder` | Runtime-managed | 编码与工程任务 |

关键澄清：

- Qwen3.5 4B 是 `reviewer_agent` 的独立 Platform-managed model deployment；
- 它不是 Knoa 默认主模型，不是 Knoa 的 fallback，也不是 Codex 的模型；
- `reviewer_agent` 是受限 System Agent，没有业务 Tool grant，不能被普通 Agent 当成通用
  子 Agent 调用；
- Codex 复用统一 Runtime SPI，但模型、认证和具体模型选择由 Codex Runtime 自己管理。

## 8. Session、Binding 与一次调用

```text
Client creates/opens Product Session
        │
        v
Conversation Turn / Task Attempt
        │
        v
AgentExecutionService
        ├── resolve AgentDefinition
        ├── resolve/persist ResolvedInvocationPolicy
        ├── ensure AgentSessionBinding
        ├── lease active Agent generation
        ├── resume/create Runtime Session
        ├── issue turn-scoped capability grant
        └── invoke Runtime Turn and relay events
```

`AgentSessionBinding` 隔离 Product Session 与 Runtime 私有 Session：

```text
Product session_handle
  -> principal_id
  -> agent_id
  -> agent_config_digest
  -> opaque runtime_session_ref
  -> binding_epoch
```

客户端和其他模块看不到 Runtime 私有会话标识。配置变化导致 definition digest 不兼容时，
空闲 Product Session 在下一 Turn rebind 新 Runtime Session；正在运行的 Turn 保持已租用的
generation，直到结束或 drain deadline。

同一 Product Session 的 Turn 在 Platform 中串行，避免并发修改同一 Runtime Session。
需要并行的工作通过独立 Child Task/Session 表达。

## 9. Delegation 与 Subagent

Subagent 的准确含义是“由父 Invocation 受控创建的 Child Task”，不是特殊进程或新的
Runtime：

```text
Parent Invocation
  -> subagent_spawn Platform Tool
  -> DelegationService
     -> check target/depth/children/parallel/deadline
     -> intersect parent policy and target Profile
     -> copy authorized Artifacts into isolated Child Session
     -> create unclaimable staged Child Task
     -> persist child ResolvedInvocationPolicy
     -> create DelegationLink
     -> activate Child Task
     -> TaskExecutor invokes target AgentDefinition
  -> join result or return detached child handle
```

核心不变量：

1. Child 权限不得超过 Parent；
2. Child 只能调用父 policy 允许的 target Agent；
3. context、Artifact、Skill、Tool 和 deadline 都显式传递并受限；
4. 所有 Child 都是 durable Task，复用已有取消、Approval、Artifact、Trace 和结果模型；
5. `join` 适合 Conversation 父调用；Task 父调用只创建 detached Child，避免占用执行槽死锁；
6. 当前是有界、浅层 delegation，不支持无限递归或 swarm。
7. staged Task 在 policy/link 完成前不能被 Executor claim；Core 重启时只恢复完整 staging，
   不完整 staging fail closed。

## 10. Conversation 与 Durable Task

Conversation 目录和 Task Definition 属于 Workspace Work，共用 Agent 执行设施，但拥有不同产品
语义。V1 Conversation 固定绑定一个 WorkspaceNode，Task 通过 `Deployment(kind=task)` 固定一个目标 Node；
Invocation/Attempt 再记录 `runs_on -> WorkspaceNode`，不增加通用 NodeExecution 状态机。

### 10.1 Conversation

```text
Conversation Session
├── bound WorkspaceNode
└── ChatTurn
    ├── input / attachments
    ├── Agent events and progress
    ├── ToolStep / Approval / Interaction
    ├── output / Artifact
    ├── AgentInvocation[] -> WorkspaceNode
    └── completed / failed / cancelled
```

Conversation 优化实时交互和跨端查看。Core 重启后，遗留的运行中 Turn 明确失败，不伪造
同一 Runtime Turn 的透明恢复。

### 10.2 Task

```text
Task Definition
├── instructions / agent_id
├── launch_policy
├── capability and notification policy
└── Deployment(kind=task) -> WorkspaceNode
    └── Node-local LaunchBinding
        └── Task Execution
    └── Attempt
        ├── AgentInvocation -> WorkspaceNode
        ├── Invocation policy snapshot
        ├── ToolStep / Approval / Interaction
        ├── Trace / Artifact
        └── result
```

Task 是持久工作。Task Definition/Deployment 由 Workspace 管理，手动、定时、Webhook、MCP event
和 delegation 在目标 Node 创建新的 Execution。定时器 materialize 在目标 Node，Hub 不运行第二套
调度状态机。

## 11. Capability Gateway 与两个权限平面

### 11.1 Platform Tool Capability Plane

所有 Built-in Tool、Platform Tool 和 MCP Tool 通过 Capability Gateway：

```text
Tool call
  -> tool existence
  -> JSON schema
  -> ResolvedInvocationPolicy allowlist
  -> principal capability
  -> argument normalization
  -> Reviewer suggestion when configured
  -> human Approval when required
  -> post-approval revalidation
  -> execution checkpoint / idempotency
  -> execute
  -> ToolResult / Artifact / audit
```

Reviewer 只提供建议，不是 enforcement point。最终授权永远由 Gateway 完成。

### 11.2 Runtime-native Capability Plane

Codex 等 Runtime 可能具有自己的文件编辑、命令执行和 sandbox 能力。这些行动不伪装成
Platform Tool，由以下约束共同治理：

```text
RuntimeSpec native capability ceiling
  ∩ Profile native capability ceiling
  ∩ ResolvedInvocationPolicy
  ∩ Runtime sandbox / approval policy
```

因此两个权限平面是明确分离但共同收窄的：

- Platform actions：Capability Gateway 强制执行；
- Runtime-native actions：Runtime/Sandbox 强制执行，Platform 决定其上限和调用条件。

## 12. Skill 与 MCP Extension

### 12.1 Skill

Skill 是 data-only 的知识和工作方法包：

- instructions；
- metadata 与触发条件；
- 所需 Tool/capability 声明；
- 可选模板和静态资源。

Skill 不是 Agent、Runtime、Tool 或权限单元。导入 Skill 后，只有当它同时满足安装状态、
Profile allowlist、本次 policy 和已有 Tool/capability 时才会激活。

发布时 Platform 对 manifest、instructions 与静态 resources 计算 canonical SHA-256；Revision
保存 `content_digest`，Provider 启动时重新校验。相同 Revision 因此不会静默读取变化后的
磁盘 Skill 内容。

### 12.2 MCP

MCP 是业务能力和业务事实的标准扩展边界：

- MCP Server 拥有领域模型、领域规则、Resource、Prompt、Tool 与 Elicitation；
- Platform 拥有 Task、Approval、Notification、Agent、权限和执行生命周期；
- Agent 只能通过 Platform 提供的受控 MCP grant 访问 MCP Tool；
- MCP Resource inventory/notification 可经 bridge 匹配已有 Task launch policy，但 MCP
  Server 不能直接创建 Knoa Task Definition。

Skill 与 MCP 必须分离：Skill 说明“如何组合能力”，MCP 提供“实际能力和事实”。

## 13. 配置控制面

### 13.1 配置分层

```text
BootstrapConfig
  └── 仅用于找到 runtime root、SQLite、secret 和首次导入来源

ManagedConfig
  └── providers / models / runtime specs / profiles / agents
      approval review / skills / MCP / operational limits

RuntimeState
  └── desired/applied revision、apply status、active/draining generation
```

SQLite Config Registry 初始化后是 ManagedConfig 的唯一真相。YAML 只负责首次导入，不在
后续启动时覆盖 Registry，也不作为运行时双真相。

### 13.2 发布状态机

```text
Mobile Configuration Page
        │ owner-only API
        v
Create Draft
  -> optimistic edit
  -> schema validate
  -> dependency/health preflight
  -> inspect diff
  -> publish desired Generation + digest
  -> apply
  -> desired_generation == applied_generation
```

失败时保留：

- 当前 Published Spec、generation/digest 和审计信息；
- `desired_generation`；
- 上一个仍在服务的 `applied_generation`；
- 稳定错误码和 `apply_status=failed`。

V1 不提供版本树或 rollback。新 generation 未通过健康检查时不切换，旧 active generation 继续服务。

### 13.3 热生效分类

| 配置变化 | 生效策略 |
| --- | --- |
| policy-only Profile、Agent enabled/default | 在发布屏障内替换 resolver/manager；不重建无关 Runtime |
| 模型执行参数、Prompt、RuntimeSpec、Codex sandbox/home | 构建受影响 Agent generation、health check、切换、旧 generation bounded drain |
| Skill/MCP set 未变化 | 不重载 Extension |
| Skill/MCP set 变化 | 预检后在发布屏障内替换；旧 grant 绑定 Tool fingerprint，变化时 fail closed |
| 不支持 reload 的 Node 组件、监听地址、TLS | 目标 Node drain 后重启受影响服务；不伪装为热生效 |
| runtime root、根密钥、process identity、设备 reboot | 目标 Node 的 host action，需要本机管理员确认 |

每个 AgentDefinition 最多一个 active generation 和一个短期 draining generation，不维护
任意历史 Runtime pool。

## 14. 配置管理页面

移动端 `app/settings/system.tsx` 是当前配置控制面 UI，主要区域包括：

- Overview：desired/applied generation、apply status、generation health；
- Agents：启停、默认 Agent、Runtime/Profile 组合；
- Models & Runtimes：模型绑定、模型所有权、并发和 Codex capability bundle；
- Profiles：instructions 与 delegation limits；
- Approval Reviewer：off/suggest/auto、timeout 和自动审批风险上限；
- Operational：迭代、Tool call、输出、上下文和 generation drain limits；
- Skills & MCP：安装/启用状态和 MCP 配置；
- Draft/Publish：编辑、validate、preflight、diff、summary、publish；
- Apply actions：hot apply、重启受影响 Node 组件和高级 Node service restart；Workspace 本身不可重启。

App 不直接操作 SQLite 或 Runtime。所有写入均经过：

```text
React Native page
  -> typed GatewayClient
  -> owner-only Gateway route
  -> Core configuration command
  -> ConfigurationService
  -> Config Registry + apply coordinator
```

## 15. Secure Gateway 与 Client 边界

Gateway 负责远程边界上的机制，而不是业务逻辑：

- pairing grant、设备身份和 challenge-response；
- 短期 authenticated session；
- owner/principal 授权；
- rate limit、payload limit 和 TLS/public binding 检查；
- REST typed protocol 和 SSE/event feed；
- Artifact 安全下载与 Android release；
- 配置 API 的 owner-only enforcement。

Gateway route 只做协议转换、认证和错误映射，业务状态变化委托 Core typed command。Mobile、
CLI、TUI 和 Feishu 最终消费相同的 Core 语义，不各自实现 Agent 或 Task 状态机。

## 16. Mobile App 模块

```text
Expo Router hierarchical shells
├── Auth stack: login / register / recovery / local mode
├── Account shell: Workspaces / Account / Hub / App settings
├── Workspace shell: Overview / Agents / Models / Skills & MCP / Nodes / Members / Settings
│     └── Node detail: Desired State / Deployments / Capabilities / Rollout / Diagnostics
└── Work execution shell: top Chat/Tasks switcher + selected placement Node + execution status
        │
        v
Domain state providers
├── IdentityProvider
├── WorkspaceProvider
├── NodeDirectoryProvider
├── NodeSessionProvider
├── TaskReminderProvider
└── PreferenceProvider
        │
        v
Typed API / transport / security / storage
├── Hub identity/workspace clients
├── GatewayClient + direct/Relay transport
├── device identity / pairing / proof
├── context-scoped server-state cache
├── conversation drafts / task event cursor
└── Android updater
```

App 的状态分为四类：

- Workspace Registry 权威状态：Conversation 目录投影、Task Definition/Deployment、共享资源 Published Spec 与稳定目录；
- Hub 权威状态：Account session、Workspace membership、Node directory/presence 和允许保存的 projection；
- Node 权威状态：Conversation 正文/ChatTurn、TaskExecution、AgentInvocation、ExecutionAttempt、Approval 执行、Artifact bytes、Node-local Config 与 Observation；
- 本地安全状态：App installation identity、Node pinned binding、Account/Node session token；
- 本地体验状态：active Workspace、optional selected Node、草稿、缓存、主题、提醒游标。

`ActiveContext = Account + Workspace + optional Node` 是状态表达，不代表三个层级在 UI 中并列。
Node 为空时仍可进入 Workspace Work 目录和已同步管理投影；读取正文、新建 Conversation、发布 Task、
执行或 live control 要求有效目标 Node。退出当前连接只关闭 transport、订阅和短期 Node session，
不删除 Conversation/Task、不退出 Hub、不删除持久 binding/deployment。
所有 Node 服务端缓存必须至少按
`issuer/account/workspace/node` 分区，本地缓存不能覆盖服务端 revision。

Mobile 不使用持久底部导航。Chat composer、附件/语音控制、键盘和 safe area 独占底部；Account 与
Workspace 使用 Stack drill-down；Conversation/Task 顶部可以提供高频切换并显示 binding/deployment Node，
Node detail 管理 Desired/Applied/Observed。导航表现不得反向改变领域归属。

Relay transport 在交付领域层前必须按 `Content-Type` 区分文本和二进制。JSON、SSE、NDJSON 和
`text/*` 显式使用 UTF-8 `TextDecoder`；Artifact/APK 保持原始 bytes。禁止依赖 React Native
`Response(ArrayBuffer)` 的隐式文本解释，也禁止在 UI 层修复 mojibake。

## 17. SQLite 持久化归属

当前采用按领域 repository 管理 schema 的 SQLite/WAL 持久化，不引入独立 ORM 或事件库。

| 领域 | 代表性表 |
| --- | --- |
| Configuration | `config_revisions`、`config_drafts`、`config_control_state` |
| Agent | `agent_session_bindings`、`invocation_policy_snapshots`、`agent_delegations` |
| Runtime session | `runtime_sessions`、`runtime_session_transcripts`、`runtime_active_sessions` |
| Conversation | `conversation_sessions`、`conversation_turns`、`conversation_tool_steps`、`conversation_approvals` |
| Task | `tasks`、`task_executions`、`runtime_tasks`、`runtime_task_attempts`、`runtime_task_tool_steps`、`runtime_task_approvals` |
| Automation | `runtime_schedules`、`runtime_triggers` 及 event/occurrence 表 |
| Interaction | `human_interactions` |
| Artifact | `artifact_registry` |
| Context/Memory | `runtime_session_contexts`、`memories`、`episodes` |
| Gateway identity | `gateway_pairing_grants`、`gateway_devices`、`gateway_auth_challenges`、`gateway_sessions`、审计表 |

表可以共享一个 SQLite 文件，但 repository 的写入职责不因此合并。跨模块协调由应用服务完成，
不允许客户端或 Runtime 直接写表。

## 18. 关键运行时序

### 18.1 用户发送一条消息

```text
Mobile -> Gateway: create ChatTurn
Gateway -> Core: typed conversation command
Core -> ConversationService: persist queued Turn
Core -> AgentExecutionService: ExecuteAgentTurn
AgentExecutionService -> Resolver: resolve policy
AgentExecutionService -> AgentManager: lease active generation
AgentExecutionService -> CapabilityGateway: issue turn grant
AgentExecutionService -> AgentRuntime: resume/create session + run turn
AgentRuntime -> CapabilityGateway: optional Tool calls
AgentRuntime -> Core: progress/output/usage events
Core -> ConversationService: persist terminal result
Gateway -> Mobile: snapshot/SSE progress
```

### 18.2 自动审批建议

```text
Main Agent Tool call
  -> CapabilityGateway detects review policy
  -> Platform invokes reviewer_agent as system invocation
  -> reviewer_agent uses dedicated Qwen3.5 4B deployment
  -> structured allow/deny/escalate suggestion
  -> Gateway policy applies risk ceiling
  -> human Approval when still required
  -> final enforcement and Tool execution
```

Reviewer 不继承主 Agent 的业务 Tool、Session 或能力 grant。
Reviewer 通过 `AgentExecutionService.execute_system_turn()` 进入同一 Resolver、caller allowlist、
policy observer、generation lease、deadline 和 grant 流程，不再直接操作 RuntimeManager/Gateway。

### 18.3 发布 Agent 配置

```text
App edits Draft
  -> validate references and invariants
  -> preflight providers/models/runtimes/skills/MCP
  -> publish immutable Revision
  -> build candidate runtimes/providers/resolver
  -> health check
  -> atomic active generation/provider swap
  -> new invocations use new revision
  -> existing leases drain on old generation
  -> applied revision advances
```

## 19. 模块依赖规则

### 19.1 允许的方向

```text
Client/Channel
  -> Gateway/CoreClient
    -> Core command/query services
      -> Domain services/repositories
      -> AgentExecutionService
        -> Agent contracts + AgentManager
          -> concrete Runtime adapters
        -> CapabilityGateway
          -> Tool registry / MCP provider
```

Composition root 可以依赖所有具体实现，但具体实现不能反向依赖 composition root。

### 19.2 禁止的依赖

- Mobile/Channel 直接访问 SQLite；
- Gateway route 直接实现 Task、Agent 或配置状态机；
- Agent Runtime 直接访问 Platform repository；
- Profile/Skill 直接签发 capability grant；
- Reviewer 直接批准或执行 Tool；
- MCP Server 创建/取消 Platform Task、Approval 或 Notification；
- Task/Conversation 分别实现第二套 Agent 调用逻辑；
- Subagent 绕过 DelegationService 直接启动进程或复制凭据；
- 配置页面直接修改 live Runtime 对象；
- YAML 与 Config Registry 同时成为 ManagedConfig 真相。

## 20. 当前实现边界

当前已实现：

- `knoa`、`reviewer_agent`、`codex` 三个 AgentDefinition 统一经 Runtime SPI 接入；
- typed RuntimeSpec、Profile、AgentDefinition 与 ResolvedInvocationPolicy；
- Session binding、policy snapshot 和单层受治理 delegation；
- staged delegation activation、跨 Session Artifact 受预算复制与恢复；
- Capability Gateway Tool allowlist 与两个权限平面；
- invocation deadline、Gateway Tool call/Artifact input budget；
- Tool definition/origin fingerprint 与旧 grant fail-closed；
- Skill 内容 digest 冻结、Skill/MCP 受控扩展和热替换；
- SQLite Config Registry、Draft、Revision、validate、preflight、publish（现有 history/rollback 非目标产品能力）；
- active + bounded draining Runtime generation；
- drain deadline 后 Runtime interrupt，且 lease 归零前不丢失 draining generation；
- Mobile 配置页面（Agent/Runtime/Profile/Reviewer/Operational/Skill/MCP）与 owner-only Gateway API；
- Conversation、Durable Task、Automation、Artifact、Approval 与 Interaction 的统一 Core。

当前明确不做：

- 无限递归、多层自治 Agent swarm；
- 动态下载可执行 Runtime plugin；
- 通用跨组织 A2A 协议；
- 任意历史 Runtime generation 路由；
- 独立通用 Policy Engine 或配置表达式语言；
- 通用 emergency revocation registry（出现明确活动 Turn 紧急封禁需求后再引入）；
- 为所有配置宣称零中断热生效；
- 让 Skill、Profile 或 Reviewer 成为权限系统；
- 为兼容旧 `AgentConfig/config.agents` 保留双模型。

## 21. 一句话架构定义

Knoa 是一个以 Core 为状态所有者、以统一 Agent Runtime SPI 为执行合同、以
`RuntimeSpec + AgentProfile = AgentDefinition` 为 Agent 组合模型、以持久化
`ResolvedInvocationPolicy` 和 Capability Gateway 为授权边界、以 Durable Child Task
表达 Subagent、并由 SQLite Configuration Control Plane 驱动可审计热发布的个人 Agent
Platform；Mobile、CLI、TUI、Feishu 和 Webhook 都只是同一平台能力的 Channel。
