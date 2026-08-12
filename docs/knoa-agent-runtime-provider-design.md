# Knoa 统一 Agent Runtime Provider 设计

> 状态：架构草案
>
> 日期：2026-08-11
>
> 范围：统一小诺原生 Agent、Codex App Server、MCP 能力服务与现有移动端通道

## 1. 背景

Knoa 当前已经具备完整的 Agent 产品基础设施：

- `CoreServer` 提供认证后的版本化 WebSocket Core API；
- `Secure Gateway` 向移动端提供 HTTP/SSE 接口；
- `ConversationSession`、`ChatTurn`、Task、Approval 和 Artifact 形成持久产品模型；
- `AgentRuntime` 负责小诺原生 Agent 的运行、工具调用、取消和事件输出；
- `ExtensionManager` 通过 MCP 接入 Monitor 等外部能力。

Codex App Server 提供另一种完整 Agent Runtime，拥有 Thread、Turn、Item、流式事件、审批、运行中引导和中断能力。它不是普通 Tool，也不应作为一个普通 MCP Tool 嵌套在小诺原生 Agent 的 ReAct 循环中。

本设计引入统一的 Agent Runtime Provider 边界，使 Core 可以为每个 Session 选择小诺原生 Agent 或 Codex App Server，同时继续复用现有 Gateway、认证、会话、任务、审批、Artifact 和事件通道。

## 2. 设计决策

采用以下分层：

```text
Knoa App / Feishu / CLI
          |
          v
Secure Gateway / Core API
          |
          v
CoreServer（认证、所有权、持久化、控制面）
          |
          v
AgentRuntimePort（Core 面向 Agent 执行的统一接口）
          |
          v
AgentRuntimeRouter
    |                     |
    v                     v
NativeAgentRuntimeProvider  CodexAppServerProvider
    |                     |
    v                     v
Knoa AgentRuntime        codex app-server

Capability Registry
    |- Built-in Tools
    `- MCP Tools
         `- Monitor MCP Server
```

核心决策如下：

1. `CoreServer` 继续作为产品控制面和唯一对外执行入口。
2. `AgentRuntimePort` 是 Core 使用的统一 Agent 接口，不暴露提供方协议。
3. `AgentRuntimeProvider` 是可插拔 Agent Runtime 实现类型。
4. 小诺原生 Agent 和 Codex App Server 是并列 Provider，而非父子 Agent。
5. Monitor 继续作为 MCP Capability Server，不升级为 Agent Runtime Provider。
6. 移动端继续使用现有 Secure Gateway HTTP/SSE，不直接连接 Codex App Server。
7. 第一阶段不把 AgentRuntime 拆成独立网络微服务；先建立稳定模块边界。
8. Codex App Server 作为独立子进程运行，由 `CodexAppServerProvider` 通过 stdio 管理。
9. Codex 不直接连接任何绕过 Knoa policy boundary 的外部 MCP；MVP 通过 App Server `dynamicTools` 将 Tool 调用回送 Knoa，长期仅在单一审批权威可证明时通过 scoped Capability MCP Gateway 统一转发。
10. 所有 Provider 控制命令、事件和主动请求都必须具备可持久化的 correlation、幂等和恢复语义。

## 3. 术语

### 3.1 Knoa Agent API

面向小诺 App、Feishu、CLI 等产品客户端的接口。它表达 Session、Turn、Task、Approval、Artifact 和事件，不暴露 Native/Codex 的实现细节。

### 3.2 AgentRuntimePort

Core 面向 Agent 执行层的稳定应用接口。Core 只依赖该 Port，不依赖 Codex JSON-RPC、模型提供商响应或具体 ReAct 实现。

### 3.3 AgentRuntimeProvider

`AgentRuntimePort` 背后的可插拔运行时实现。Provider 负责把特定 Agent Runtime 的协议和生命周期转换成 Knoa 统一模型。

首批 Provider：

- `NativeAgentRuntimeProvider`：适配现有小诺 AgentRuntime；
- `CodexAppServerProvider`：适配 `codex app-server`。

### 3.4 Capability

Agent 可以调用的能力。Capability 分为 Built-in Tool 和 MCP Tool。Monitor 属于 MCP Tool Provider，不属于 Agent Runtime Provider。

## 4. 职责边界

### 4.1 CoreServer

CoreServer 负责：

- 认证和 principal 所有权校验；
- Knoa Session、Turn、Task 和 Approval 的持久化；
- Provider 选择和会话绑定；
- 事件排序、持久化、重放和客户端订阅；
- Artifact 所有权和保留策略；
- 工作目录授权和运行策略；
- Provider 故障恢复与用户可见状态转换。

CoreServer 不负责：

- 解析 Codex 的业务事件细节；
- 实现模型流协议；
- 直接调用 GitLab/Jira 等业务系统；
- 向移动端暴露任意 Provider 原始方法。

### 4.2 AgentRuntimeRouter

`AgentRuntimeRouter` 根据 Session 绑定选择 Provider，并统一完成：

- Provider 注册和健康状态；
- Session 到 Provider Session 的映射；
- Turn 到 Provider Turn 的映射；
- Provider 调用超时和取消；
- 原始事件到 `AgentEvent` 的转换入口；
- Provider 不可用时的失败关闭。

### 4.3 AgentRuntimeProvider

Provider 负责：

- 创建、恢复和关闭 Provider Session；
- 启动、引导和中断 Turn；
- 接收 Provider 原始流式事件；
- 转换 Provider 状态、工具、文件和审批事件；
- 管理 Provider 进程或连接生命周期；
- 报告健康状态和版本能力。

Provider 不负责：

- Knoa principal 认证；
- 公共 Session ID 分配；
- 产品级 Task、Approval 或 Artifact 持久化；
- 移动端 SSE 或通知；
- 绕过 Core 的工具和安全策略。

## 5. 统一接口

建议新增 Provider 内部协议：

```python
from collections.abc import AsyncIterator
from typing import Protocol


class AgentRuntimeProvider(Protocol):
    provider_id: str

    async def capabilities(self) -> "AgentRuntimeCapabilities": ...

    async def create_session(
        self,
        request: "ProviderSessionCreateRequest",
    ) -> "ProviderSession": ...

    async def resume_session(
        self,
        request: "ProviderSessionResumeRequest",
    ) -> "ProviderSession": ...

    async def start_turn(
        self,
        request: "ProviderTurnStartRequest",
    ) -> "ProviderTurn": ...

    async def steer_turn(
        self,
        request: "ProviderTurnSteerRequest",
    ) -> None: ...

    async def interrupt_turn(
        self,
        request: "ProviderTurnInterruptRequest",
    ) -> None: ...

    async def resolve_request(
        self,
        request: "ProviderRequestResolution",
    ) -> None: ...

    def events(
        self,
        provider_session_id: str,
    ) -> AsyncIterator["ProviderEvent"]: ...

    async def close_session(self, provider_session_id: str) -> None: ...

    async def health_check(self) -> "ProviderHealth": ...
```

并保留 Core 面向上层的 `AgentRuntimePort`。两者关系为：

```text
CoreServer
  -> AgentRuntimePort
       -> AgentRuntimeRouter
            -> AgentRuntimeProvider
```

`AgentRuntimePort` 表达 Knoa 领域操作；`AgentRuntimeProvider` 表达可替换的运行时 SPI。

## 6. Provider 能力声明

不同 Runtime 的能力不完全相同。Provider 必须显式声明能力，Core 不应通过 Provider 名称推断。

```python
class AgentRuntimeCapabilities:
    supports_resume: bool
    supports_steer: bool
    supports_interrupt: bool
    supports_fork: bool
    supports_approvals: bool
    supports_user_input_requests: bool
    supports_file_change_events: bool
    supports_reasoning_events: bool
    supports_workspace: bool
    supports_models: bool
    supports_skills: bool
    supports_mcp: bool
```

当客户端请求 Provider 不支持的操作时，Core 返回稳定的 `capability_not_supported`，而不是静默忽略或模拟不可靠语义。

## 7. 数据模型

### 7.1 Session 绑定

Knoa 的 `session_handle` 与 Provider Session ID 必须分离。建议增加独立绑定记录，而不是把 Provider 字段扩散到所有 Conversation 模型。

```text
agent_session_bindings
----------------------
session_handle
principal_id
provider_id
provider_session_id
workspace_root
model
approval_policy
sandbox_policy
provider_config_json
binding_epoch
owner_lease_id
owner_lease_expires_at
state
created_at
updated_at
revision
```

示例：

```json
{
  "session_handle": "knoa_session_001",
  "provider_id": "codex",
  "provider_session_id": "thr_123",
  "workspace_root": "/home/user/project",
  "model": "gpt-5.6-terra",
  "approval_policy": "on_request",
  "sandbox_policy": "workspace_write"
}
```

产品客户端只看到 `session_handle`，不依赖 `thr_123`。

Provider 绑定规则：

1. 现有无 Provider 字段的 Session 在启用 Provider 选择前原子回填为 `native`；
2. Session 创建首个 Turn 后，`provider_id` 和 `provider_session_id` 不可静默更换；
3. 每次恢复都必须持有 `owner_lease_id` 和 `binding_epoch`，旧 Worker 或旧 Core 实例的命令被 fencing；
4. 同一 Session 同时只能有一个活动 Turn；
5. Core 启动时扫描未释放的 lease 和孤儿 Provider 进程，先标记状态再决定恢复或中断。

### 7.2 Turn 绑定

```text
agent_turn_bindings
-------------------
turn_id
session_handle
provider_id
provider_turn_id
binding_epoch
state
started_at
finished_at
revision
```

### 7.3 Provider 请求绑定

Provider 主动请求审批、权限确认或用户输入时，需要先持久化为 Knoa 领域对象，再通知客户端。不能把所有请求压缩成 `approved: bool`，因为 Codex 的用户输入和结构化 elicitation 需要返回带类型的内容。

```text
agent_provider_requests
-----------------------
request_id
session_handle
turn_id
provider_id
provider_request_id
request_type
payload_json
resolution_schema_json
request_epoch
state
created_at
resolved_at
resolved_by
```

`request_type` 至少包括：

```text
tool_approval
permission_approval
user_input
mcp_elicitation
```

每种类型都有对应的 resolution schema。请求只能被同一个 `request_id + request_epoch` 成功解决一次。

## 8. 统一事件协议

Provider 原始事件不得直接进入 Gateway。所有事件转换成 `AgentEvent`：

```python
class AgentEvent:
    event_id: str
    seq: int
    session_handle: str
    turn_id: str
    event_type: str
    payload: "BoundedEventPayload"
    created_at: float
```

公共事件 payload 必须是按事件类型区分的有界模型，而不是无限制 `dict`。转换层在写库和推送前执行大小限制、字段 allowlist、Provider 元数据脱敏和 reasoning 策略过滤。超限输出转为 Artifact 或摘要，并发出 `provider.warning`，不得把原始巨量内容写入事件日志。

第一版事件类型：

```text
session.started
session.resumed
session.status_changed
session.closed

turn.started
turn.steer_requested
turn.steer_accepted
turn.steer_rejected
turn.completed
turn.interrupt_requested
turn.interrupt_accepted
turn.interrupt_rejected
turn.interrupted
turn.failed

assistant.delta
assistant.completed
reasoning.delta
plan.updated

tool.started
tool.progress
tool.completed
tool.failed

file.changed
artifact.created
context.compacted

approval.requested
approval.resolved
user_input.requested
user_input.resolved

provider.warning
provider.disconnected
provider.recovered
```

### 8.1 快照与事件并存

Knoa 当前 ChatTurn 以完整快照为主，Task 以持久事件为主。目标设计同时保留二者：

- `AgentEvent` 用于低延迟增量推送和精确重放；
- `ChatTurnSnapshot` 用于断线恢复、首次加载和状态校验；
- 每次事件事务提交后更新对应快照 revision；
- 客户端发现 seq 缺口时重新读取快照，再从新游标继续订阅。
- Provider 事件以 `(provider_id, provider_session_id, provider_turn_id, provider_event_id, binding_epoch)` 作为去重相关键；重复事件不得生成新的公共 seq；
- 如果 Provider 没有稳定事件 ID，适配器必须为可去重事件生成稳定 fingerprint；无法可靠去重时进入 reconciliation，不得直接追加事件；
- Turn binding、首个 Provider event 和 Knoa snapshot 更新必须有明确的事务边界；
- 重连、重复、乱序和 Core 崩溃恢复必须有 conformance tests。

## 9. Native Provider 映射

```text
Native AgentRuntime             Knoa AgentEvent
------------------------------------------------
content_delta                -> assistant.delta
final_output                 -> assistant.completed
reasoning_delta              -> reasoning.delta
plan                         -> plan.updated
tool_call                    -> tool.started
tool_result                  -> tool.completed / tool.failed
artifact                     -> artifact.created
context_compacted            -> context.compacted
warning                      -> provider.warning
AgentRuntime.cancel()        -> turn.interrupted
```

Native Provider 继续使用现有 SessionManager、ReActLoop、ToolStep 和 RuntimeEvent，不需要通过网络调用自身。

## 10. Codex App Server Provider 映射

`CodexAppServerProvider` 第一阶段通过 stdio 启动和管理一个本地子进程：

```text
CoreServer
  -> CodexAppServerProvider
       -> stdin/stdout JSONL
            -> codex app-server
```

连接状态机为 `SPAWNED -> INITIALIZING -> READY`。只有 `initialize` 成功响应且 schema/capability 校验通过后才能发送 `initialized` 并进入 READY。stdout 只接受有界 JSONL；stderr 独立采集；畸形、超长或未知主动请求 fail closed。

主要调用映射：

```text
Provider create_session      -> thread/start
Provider resume_session      -> thread/resume
Provider start_turn          -> turn/start
Provider steer_turn          -> turn/steer
Provider interrupt_turn      -> turn/interrupt
Provider close_session       -> thread/unsubscribe 或本地卸载
```

每个控制操作都带有 Knoa `command_id` 和 Provider correlation。Provider 只能返回 `accepted`、`rejected` 或 `unknown`；Core 只有在收到 Provider 接受确认后才记录 `turn.steer_accepted` 或 `turn.interrupt_accepted`。最终状态仍以 `turn/completed` 为准。

主要事件映射：

```text
Codex notification                 Knoa AgentEvent
------------------------------------------------------------
thread/started                  -> session.started
thread/status/changed           -> session.status_changed
turn/started                    -> turn.started
item/agentMessage/delta         -> assistant.delta
item/started                    -> 对应 item 类型的 started 事件
item/completed                  -> 对应 item 类型的 completed 事件
turn/completed                  -> turn.completed/interrupted/failed
权限审批 server request          -> approval.requested
tool/requestUserInput           -> user_input.requested
```

Provider 必须使用当前安装版本生成的 Codex App Server schema，不手写假定长期稳定的字段：

```bash
codex app-server generate-json-schema --out <managed-schema-dir>
```

生成结果属于运行时兼容资料，不直接成为 Knoa 的公共协议。

Codex App Server 的账户认证只解决上游 Codex 凭据，不提供 Knoa principal 隔离。所有主动请求必须通过持久 Thread binding 反查 principal、Session 和 workspace；App Server 进程、配置和凭据至少不得跨不可信 principal 共享。

## 11. Session 和 Turn 生命周期

### 11.1 新建 Codex Session

```text
App 创建 Knoa Session，选择 provider=codex 和 workspace
  -> Core 校验 principal 对 workspace 的访问权
  -> 创建 Knoa session_handle
  -> CodexProvider thread/start
  -> 持久化 provider_session_id
  -> 返回 Knoa Session Snapshot
```

### 11.2 开始 Turn

```text
App create_chat_turn
  -> Core 创建 pending ChatTurn
  -> AgentRuntimeRouter 选择绑定的 Provider
  -> Provider start_turn
  -> 持久化 provider_turn_id
  -> Provider 事件转换并写入事件日志
  -> 现有 SSE/Core 订阅向客户端推送
```

### 11.3 运行中追加指令

新增 Core 领域操作 `steer_chat_turn`：

```text
App steer_chat_turn
  -> Core 校验 Turn 仍在运行
  -> Provider capabilities.supports_steer
  -> 生成幂等 command_id
  -> Provider steer_turn
  -> 记录 turn.steer_requested
  -> 收到 accepted 后记录 turn.steer_accepted
```

Native Provider 第一阶段可声明不支持 steer；Codex Provider 映射到 `turn/steer`。

### 11.4 中断和继续

```text
cancel_chat_turn
  -> 生成幂等 command_id
  -> Provider interrupt_turn
  -> Turn 进入 interrupt_requested
  -> accepted 后进入 interrupting
  -> 收到最终 Provider 事件后进入 interrupted
  -> 超时或连接丢失进入 interrupt_unknown，并要求显式恢复/重试决策
```

中断后的继续不是重试旧 Turn，而是在相同 Session 中创建新 Turn。`retry_chat_turn` 保留为产品级重放语义，但不替代 `resume_session + start_turn`。

## 12. 审批桥接

审批必须属于 Core，而不是某条 WebSocket 或 stdio 连接。

Codex 发起审批或用户输入请求时：

```text
Codex server request
  -> CodexProvider 暂存 JSON-RPC correlation
  -> Core 创建持久 ProviderRequest 子类型
  -> 写入 approval.requested 或 user_input.requested
  -> App 通过现有通道显示对应类型的表单
  -> Core 校验 principal、request_epoch 和幂等状态
  -> Provider resolve_request（typed resolution）
  -> 写入 approval.resolved 或 user_input.resolved
```

安全规则：

1. 未知、过期或重复审批失败关闭；
2. Provider 请求内容只能作为展示信息，不能自行提升本地权限；
3. Core 的本地 policy 决定是否允许、是否需要确认及授权范围；
4. App Server 连接在审批期间断开时，审批标记为 provider_lost，不假设操作已执行；
5. 对结果不确定的外部副作用必须进入 outcome_unknown 或失败状态，不自动重放。
6. MVP 只允许单次最小范围的 `accept`、`decline` 或 `cancel`；拒绝 `acceptForSession`、execpolicy amendment、session-scoped permission 和超出请求子集的授权。
7. dynamic tool/server request 在执行前持久化 `threadId/turnId/itemId/callId/JSON-RPC id/tool idempotency key`；断线不能靠 `thread/resume` 恢复旧请求。

## 13. MCP 和 Monitor 的位置

Monitor 保持当前独立 MCP Server 设计：

```text
GitLab/Jira -> Monitor Poller -> Monitor SQLite
                                  ^
Agent Capability Call -> MCP -----+
```

MCP 与 Agent Runtime Provider 的边界：

```text
AgentRuntimeProvider：谁来运行 Agent，如何开始、引导、中断和恢复
MCP Server：Agent 可以调用哪些查询或业务动作
```

### 13.1 Native Provider

Native Provider 继续通过 Knoa `ExtensionManager` 和 `Capability Registry` 使用 Monitor MCP。

### 13.2 Codex Provider

Codex App Server 有两种接入 Knoa MCP 能力的候选方式。生产 Provider 必须使用隔离、生成式 Codex 配置目录，禁用非 Gateway 的 MCP、apps 和 plugins；直接在 Codex 配置中注册 Monitor/Jira/PC Assistant MCP 会绕过 Knoa Capability Registry、ToolStep、统一审批和 principal 审计，因此无论当前工具是否只读都不允许。启动后必须通过 `mcpServerStatus/list` 核验 inventory，发现额外外部能力即 fail closed。

#### MVP：dynamicTools 回送 Knoa

Codex App Server 的 `dynamicTools` 是实验性接口。Knoa 在 `thread/start` 时仅向当前 Thread 注册已批准的工具定义；当 Codex 使用工具时，App Server 通过 `item/tool/call` 将请求回送给 `CodexAppServerProvider`，再由 Knoa 执行：

```text
Codex Agent
  -> Codex App Server dynamicTools
       -> item/tool/call
            -> CodexAppServerProvider
                 -> Knoa Capability Registry
                      -> ToolStep / Approval / Idempotency
                           -> Monitor MCP
```

该路径的工具授权绑定到 `principal_id + session_handle + binding_epoch`，Codex 不接触 Monitor 凭据。MVP 阶段仅允许声明式、大小受限的工具 schema；未注册或高风险未审批工具必须失败关闭。

`dynamicTools` 只导出 Capability Registry 的 Tool descriptors/calls；MCP Resources、Prompts、Notifications 和 subscriptions 仍由 Knoa Standard MCP Host 处理，不得复制进 Provider。

#### 长期：Knoa Capability MCP Gateway

为了让其他 Agent Runtime 也能复用同一能力集合，增加 principal/session-scoped 的 `Knoa Capability MCP Gateway`。Codex 配置连接 Gateway，而不是直接连接 Monitor：

目标结构：

```text
Codex App Server
  -> Knoa Capability MCP Gateway
       -> Knoa Capability Registry
            |- Built-in Tool
            `- Monitor MCP
```

Gateway 使用短期、Session-scoped 凭据将每次调用绑定到 Knoa Session，并将 MCP 调用重新送回 Capability Registry。统一执行策略、审批、幂等和审计。

Knoa 必须是 Gateway 外部 Tool 的唯一审批权威。若无法可靠关闭或自动拒绝 App Server 对同一 MCP action 的内建 approval，则不得实现该 Gateway 路径。一个外部 action idempotency key 最多产生一次用户审批。

该 Gateway 是能力代理，不是 Agent 控制协议，也不替代 `AgentRuntimePort`。

## 14. 进程和部署模型

### 14.1 第一阶段：同机模块化

```text
pc-assistant CoreServer 进程
  |- AgentRuntimeRouter
  |- NativeAgentRuntimeProvider（进程内）
  `- CodexAppServerProvider
       `- codex app-server 子进程
```

优点：

- 复用当前认证、存储和事件基础设施；
- 不增加新的内部网络协议；
- Session/Approval 事务仍由 Core 单点拥有；
- Codex 故障通过子进程边界隔离。

“独立子进程”不等于自动完成资源隔离。Codex Provider 必须由 supervisor 管理，并至少具备：

- 每个 Provider/Session 的并发上限；
- bounded stdin/stdout/event queues 和 backpressure；
- CPU、内存、文件描述符和子进程数配额；
- 启动、空闲、Turn 和审批超时；
- parser deadlock 检测、kill、restart 和 circuit breaker；
- Provider 进程退出时对所有活动 Turn 进行明确的 `provider_disconnected` / `outcome_unknown` 转换。

在没有这些限制前，Phase 2 只允许受控的单 Session 或低并发试运行，不宣称 Provider 故障不会影响 Core 或其他 Session。

### 14.2 第二阶段：本地 Agent Worker

当 Core 与代码工作区不在同一台机器时：

```text
Knoa Cloud/Core
  -> 认证后的反向长连接
       -> 用户电脑上的 Knoa Agent Worker
            -> Codex App Server
            -> 本地项目目录
```

Worker 主动连接 Core，避免暴露用户电脑入站端口。Worker 只接受 Core 签发的短期、Session-scoped 工作负载，不持有移动端身份凭据。

### 14.3 第三阶段：独立 Agent Runtime Service

只有出现以下需求时才提取：

- Native/Codex 需要独立扩缩容；
- 需要多主机工作区调度；
- Agent 运行资源明显影响 Core；
- 需要独立升级或重启 Runtime；
- 需要语言或运行环境隔离。

提取时保持 `AgentRuntimePort` 语义不变，只增加传输适配器，不让 Core 或 App 感知部署变化。

## 15. 安全模型

### 15.1 工作区授权

- App 不提交任意未验证的文件系统路径；
- Core 从已登记 workspace 中选择真实路径；
- 路径必须规范化并限制在允许根目录；
- Session 绑定后改变 workspace 需要显式高风险操作；
- Provider 只接收 Core 已批准的 workspace 和 sandbox policy。

### 15.2 Provider 权限

- 默认使用 workspace-write 或更严格的沙箱；
- 禁止通过公共 Gateway 暴露任意 `shellCommand`、`process/spawn` 等原始能力；
- Provider 原始描述和 annotations 不是本地授权依据；
- 外部副作用仍通过 Knoa 统一确认、幂等和审计边界；
- Codex 登录凭据保留在运行 Codex 的机器上，不发送给 App。
- Codex 使用隔离配置目录；非 Gateway MCP、apps 和 plugins 默认禁用；
- Session grant、execpolicy amendment 和扩大 workspace/network scope 默认拒绝。

### 15.3 传输

- 同机 Codex 优先使用 stdio；
- 不直接向公网暴露 Codex App Server WebSocket；
- 远程 Worker 使用 TLS、短期凭据和服务端工作负载授权；
- App 继续只连接 Secure Gateway。

## 16. 错误和恢复语义

统一错误分类：

```text
provider_unavailable
provider_protocol_error
provider_version_unsupported
session_not_resumable
turn_not_active
capability_not_supported
approval_expired
workspace_denied
connection_lost
command_rejected
command_unknown
provider_resource_exhausted
outcome_unknown
internal_error
```

恢复原则：

1. 查询操作可以有界重试；
2. 创建 Session/Turn 使用幂等 client request ID；
3. 已确认没有副作用的中断操作可以重试；
4. 外部副作用结果不明时不得自动重放；
5. Provider 重连后通过 Session binding 恢复，不能依赖“最近一次会话”；
6. 事件以 Core 持久化 seq 为最终客户端游标，不使用 Provider 自身序号作为公共游标。
7. steer/interrupt 等控制命令必须使用 command ID 幂等执行，并在 deadline 到期后进入 `command_unknown`，不得无限停留在非终态。
8. Provider 重连后的恢复必须先通过 Provider history/read 或等价状态校验完成 reconciliation，再恢复实时事件消费。

## 17. 可观测性

每次 Provider 操作至少记录：

```text
principal_id（日志中使用安全标识）
session_handle
turn_id
provider_id
provider_session_id（必要时脱敏）
provider_turn_id
operation
duration_ms
terminal_state
error_code
event_count
```

禁止记录：

- Codex bearer token；
- GitLab/Jira/MCP 凭据；
- 未脱敏的配对和 Gateway Session token；
- 不必要的完整用户输入或文件内容；
- Provider 内部隐藏推理。

## 18. 公共接口演进

现有 Gateway 接口保持兼容，新增最小字段和操作：

```text
POST /conversation-sessions
  provider_id?
  workspace_id?

POST /conversation-sessions/:id/turns
  继续创建 Turn

POST /chat-turns/:id/steer
  运行中追加输入

POST /chat-turns/:id/cancel
  中断当前 Turn

POST /approvals/:id/resolve
  解决 Provider/Tool 审批

GET /agent-runtime/providers
  返回可选 Provider 和能力声明
```

公共 API 不返回 Provider 原始 JSON-RPC，不允许客户端调用任意 Provider 方法。

## 19. 实施阶段

### Phase 1：接口和 Native 收口

- 定义 `AgentRuntimeProvider`、能力声明和 Provider 事件；
- 增加 `AgentRuntimeRouter` 和 `AgentRuntimeRegistry`；
- 用 `NativeAgentRuntimeProvider` 包装现有 AgentRuntime；
- 保持现有 App 行为和测试不变；
- 增加 Session/Turn binding 存储、binding epoch 和 owner lease；
- 定义 ProviderRequest 的多态审批/用户输入模型；
- 定义有界事件 payload、Provider event 去重键和 Core 事件事务；
- 为 Native/Codex Provider 编写相同的 conformance tests。

### Phase 2：Codex App Server MVP

- 实现 stdio JSONL 客户端和初始化握手；
- 实现 thread create/resume、turn start/interrupt；
- 转换消息、工具、文件和 terminal 事件；
- 复用 ChatTurn snapshot 与现有 SSE；
- 增加 Provider 版本和 schema 检查；
- 增加完整 initialize 状态机、有界 JSONL parser 和隔离 Codex 配置；
- 仅支持本机已登记 workspace；
- 只通过 `dynamicTools` 暴露当前 Session 已批准的能力；
- Codex 不得直接使用任何绕过 Knoa policy boundary 的外部 MCP；
- 以 supervisor、bounded queues 和低并发配额运行。

### Phase 3：交互完整性

- 增加 turn steer；
- 增加持久 Approval 和 user-input request 桥接；
- 增加事件日志和 seq 重放；
- 增加 Provider 断线、重启和 Session 恢复；
- 增加 Native/Codex 能力差异 UI；
- 增加 steer/interrupt command 状态机、deadline 和 outcome_unknown；
- 增加 dynamic tool/server request 持久桥接状态与 provider_lost/expired；
- 增加重复、乱序、崩溃和审批断线测试。

### Phase 4：统一 Capability Gateway

- 为外部 Runtime 提供 scoped MCP Gateway；
- Codex 通过 Gateway 使用 Knoa Capability Registry；
- 统一 Monitor 等 MCP Tool 的策略、审批和审计；
- 禁止未登记工具和凭据越界；
- 证明 Knoa 是外部 Tool 唯一审批权威，否则不启用 Gateway；
- 通过 Native 与 Codex 的同一工具调用验收证明策略、审批、幂等和审计一致。

### Phase 5：可选 Agent Worker

- 定义 Core 与 Worker 的内部传输适配；
- 支持用户电脑上的反向安全连接；
- 增加 workspace 注册、在线状态和工作负载租约；
- 保持 Gateway 和 `AgentRuntimePort` 语义不变。

## 20. 非目标

- 不把 Codex 当作普通 MCP Tool；
- 不让小诺原生 Agent 默认嵌套调用完整 Codex Agent；
- 不让移动端直接连接 Codex App Server；
- 不把 Monitor 变成 Agent Runtime；
- 不向公共 Gateway 暴露任意 Provider 方法；
- 不立即拆分独立 Agent 微服务；
- 不建立第二套绕过 Core 的审批或 Artifact 存储；
- 不保证 Knoa 线协议与 Codex App Server 线协议兼容。

## 21. 验收目标

设计实现完成后应满足：

1. 同一个小诺 App 可以创建 Native 或 Codex Session；
2. App 的会话、Turn、SSE 和审批交互不依赖 Provider；
3. Native 和 Codex 事件转换为同一 `AgentEvent` 集合；
4. Codex 可以被可靠中断，并在已记录 Thread 上继续新 Turn；
5. Provider ID、Session ID 和 Turn ID 不泄漏为公共主键；
6. Monitor 继续作为独立 MCP Server 工作；
7. Codex 工具调用通过 dynamicTools 或 scoped Capability MCP Gateway 回到 Knoa，不能绕过 Capability Registry；
8. 所有 Provider 主动请求都能以正确类型持久化、显示、解决和幂等重放；
9. Provider 事件有界、去重、全序并可在 Core 崩溃/重连后恢复；
10. steer/interrupt 具有 accepted/rejected/unknown 结果和超时收敛；
11. Provider Session 绑定不可越权替换，并由 lease/epoch 防止并发接管；
12. Codex Provider 资源受 supervisor 和配额约束，故障不会导致 CoreServer 或其他 Session 失控；
13. 所有 workspace、工具和外部副作用仍经过 Knoa 本地策略；
14. 未来拆分 Agent Worker 时，不需要重写 App 或产品 Gateway。

## 22. 结论

Knoa 的统一框架应围绕 `AgentRuntimePort + AgentRuntimeProvider` 建立：

```text
Knoa Agent API
  -> CoreServer
       -> AgentRuntimePort
            -> AgentRuntimeRouter
                 |- NativeAgentRuntimeProvider
                 `- CodexAppServerProvider

Capability Registry
  |- Built-in Tools
  `- MCP Servers
       `- Monitor
```

Agent Runtime Provider 统一“谁来运行 Agent，以及如何开始、引导、中断和恢复”；MCP 统一“Agent 可以调用哪些能力”；`dynamicTools`/Capability MCP Gateway 负责把外部 Runtime 的能力调用收回 Knoa 策略边界；Secure Gateway 统一“产品客户端如何安全使用这些能力”。三者分层后，小诺可以接入 Codex，而不牺牲现有的移动端、持久任务、审批、安全和扩展体系。
