# Knoa 统一 Agent Runtime 设计

> 状态：实施中（Phase 1、Phase 2 主路径与 Codex MVP 已落地）
>
> 日期：2026-08-12
>
> 范围：Knoa Platform、Knoa Agent、Codex App Server、Conversation、Task 与 MCP 能力接入
>
> 迁移策略：直接切换；不兼容旧代码、旧数据库 schema 或旧运行数据

## 1. 结论

Knoa 产品由两个高内聚部分组成：

```text
Knoa = Knoa Platform + Knoa Agent
```

- **Knoa Platform**：产品控制面、会话与任务、身份权限、能力治理、持久化和客户端通道；
- **Knoa Agent**：Knoa 自研的 Agent 实现，负责模型上下文、Prompt 组装、推理循环、工具使用策略和上下文压缩。

目标结构收敛为：

```text
Knoa App / Feishu / CLI
          |
          v
Knoa Platform
  |- Secure Gateway / Core API
  |- Conversation / Task / Approval / Artifact
  |- Capability Plane
  |    |- Platform Capability MCP Gateway
  |    |- Built-in capability handlers
  |    `- Standard MCP Host -> external MCP servers
  `- Artifact Store / Artifact MCP Resources
          |
          v
ConversationService / TaskExecutor
          |
          v
AgentExecutionService            # 路由、持久化和领域编排
  `- AgentManager                # 配置、实例、健康、容量、生命周期
       |- KnoaAgentRuntime       # 直接实现 AgentRuntime
       `- CodexAgentRuntime      # 直接实现 AgentRuntime

Neutral Agent Contract Package
  `- AgentRuntime                # Host -> Agent

Platform Capability Plane
  |- Built-in Platform Tools/Resources
  `- Upstream MCP Servers
       `- Jira / Monitor / Knoa
```

关键变化：

1. 删除名称 `AgentRuntimePort`。它实际表达的是 Core 的“执行 Agent Turn”用例，而不是一个 Agent Runtime。
2. Core 侧只保留具体应用服务 `AgentExecutionService`，不再额外建立一个只有单实现的 `AgentExecutor` 架构层。
3. 删除 `AgentRuntimeProvider` 概念，统一 SPI 直接命名为 `AgentRuntime`。
4. 删除 `NativeAgentRuntimeProvider` 薄适配层。现有内置实现正向重构为 `KnoaAgentRuntime`，直接实现 `AgentRuntime`。
5. Codex 实现命名为 `CodexAgentRuntime`，在内部适配 Codex App Server JSON-RPC。
6. 删除只有转发作用的 `AgentRuntimeRouter`；路由是 `AgentExecutionService` 的一项职责。
7. `knoa` 是稳定的 Agent 身份，不是“当前默认值”的别名；`default_agent` 只是一个可修改的配置指针。
8. 首批只支持 `agent_id=knoa` 和 `agent_id=codex`，不引入 `runtime_kind`、`profile`、`catalog` 或任意插件类型系统。
9. `KnoaAgentRuntime` 不依赖 Knoa Platform 模块；Agent 与 Platform 只共同依赖中立、版本化的 Agent Contract Package。
10. built-in tools、外部 MCP 与 Artifact 访问都由 Platform 治理；Agent 只作为 MCP Client 使用当前 Session 获得的标准 MCP endpoint，不接收 Tool callback、Platform repository 或主机文件路径。
11. Platform 文件、图片和 Agent 输出通过类型化 Content/Artifact 契约传递；产品 Artifact 归 Platform，Agent 私有临时文件和 checkpoint 归 Agent。

这是 greenfield target design。实现时允许删除旧表、旧事件、旧 Session/Task 数据和旧 Runtime 代码后直接建立新结构；不设计兼容层、双写、回填、旧数据读取或渐进迁移。正确方向是让 Knoa 与 Codex 两个 Runtime 都遵守清晰的 Session、Turn、事件、交互和恢复语义。

## 2. 为什么要重新命名

### 2.1 为什么删除旧 `AgentRuntimePort`

旧接口：

```python
class AgentRuntimePort(Protocol):
    def run(context, request) -> AsyncIterator[RuntimeEvent]: ...
    async def cancel(scope, request) -> CancelResult: ...
    async def health_check() -> HealthStatus: ...
```

它混合了 Conversation/Task 用例与一次 ReAct 执行，而且只有两个直接消费者：

- `ConversationService`
- `TaskExecutor`

消费者准备产品 Session 历史、审批、工具提交和持久化回调，然后请求执行一次 ReAct run。因此它既不是完整 Runtime SPI，也不值得保留为兼容边界。

继续叫 `AgentRuntimePort` 会产生三个问题：

- 与真正的 `AgentRuntime` 实现边界重名；
- 容易把 Codex App Server 的 Thread/Turn/Item 生命周期压缩成一次 `run()`；
- 容易再引入一个与它方法重复的 `AgentRuntimeProvider`。

因此不再一对一重命名这个 Protocol，而是由具体的 `AgentExecutionService` 承接执行用例。Python 测试可通过注入 fake `AgentRuntime` 或 fake runtime resolver 隔离，无需为了 mock 再制造一个产品架构层。如果未来 `AgentExecutionService` 真正出现第二个独立实现，再从真实消费者抽取窄 Protocol。

### 2.2 为什么不保留 Provider

“Provider”容易与 LLM provider、model provider、MCP server 混淆。对于 Knoa 来说，小诺和 Codex 都是完整 Agent Runtime，并不存在还要再包装一层 Provider 的业务意义。

目标代码关系应是：

```text
KnoaAgentRuntime  implements AgentRuntime
CodexAgentRuntime implements AgentRuntime
```

而不是：

```text
NativeAgentRuntimeProvider -> AgentRuntime -> ReActLoop
```

如果一个适配类只转发相同方法、没有协议转换或策略职责，它不应存在。

## 3. 外部设计依据

### 3.1 Codex App Server

OpenAI 官方 App Server 文档显示，Codex 的稳定核心不是一次 `run()`，而是：

- 连接级 `initialize` / `initialized`；
- Thread 创建、恢复、读取和订阅；
- Turn 启动、运行中 `steer` 和 `interrupt`；
- Item 与增量通知；
- Agent 向 Client 发起的审批、用户输入、dynamic tool 等双向请求；
- 版本生成的 TypeScript / JSON Schema；
- 断线后基于 Thread 的恢复，但连接级待决 server request 不能仅靠 Thread resume 恢复。

参考：<https://developers.openai.com/codex/app-server/>

### 3.2 Agent Client Protocol（ACP）

ACP 同样区分：

- initialize 时协商协议版本和双方 capability；
- session/new、session/load、session/resume；
- session/prompt 作为一次 Turn；
- session/update 作为增量输出；
- Agent 反向请求 Client 的权限、文件系统、终端和 elicitation；
- prompt 的响应给出最终 stop reason。

这说明“命令 + 增量事件 + 最终结果 + 双向交互”是比单次 token stream 更稳健的 Runtime 边界。

参考：

- <https://agentclientprotocol.com/protocol/v1/overview>
- <https://agentclientprotocol.com/protocol/v1/initialization>
- <https://agentclientprotocol.com/protocol/v1/prompt-turn>

### 3.3 LangGraph

LangGraph 的 durable execution 强调：

- Thread ID 是恢复持久状态的指针；
- checkpoint 与长期 store 是不同所有权；
- interrupt 必须先持久化状态，之后用同一 Thread 恢复；
- 恢复可能重新执行 interrupt 之前的代码，因此副作用必须有幂等或明确提交点。

这支持 Knoa 将 binding、interaction 和 command 状态先落库，再调用 Runtime；也支持把恢复建模为显式 reconciliation，而不是盲目重试。

参考：

- <https://docs.langchain.com/oss/python/langgraph/persistence>
- <https://docs.langchain.com/oss/python/langgraph/interrupts>

### 3.4 A2A

A2A 主要解决跨组织或跨服务 Agent 之间的发现、消息、Task、Artifact 和推送通知。它适合未来 Knoa 与远程 Agent Service 的外部互操作，但不适合作为当前进程内 Python Runtime SPI：

- 它的 Task 是远程 Agent 的业务任务对象；
- Knoa 已经拥有自己的 Product Task、Session、Approval 和 Artifact；
-直接套用会产生双重领域模型和双重持久化权威。

参考：<https://a2a-protocol.org/latest/specification/>

## 4. 设计原则

1. **Platform 是产品事实权威。** Conversation 原始消息、Product Task、Approval、Artifact、公共事件序号和 principal ownership 只由 Knoa Platform 持久化。
2. **Agent Runtime 是执行语义权威。** 模型上下文选择、Prompt 组装、上下文压缩、推理循环、底层 Thread 和活动执行状态由对应 Agent Runtime 管理。
3. **产品 ID 与 Runtime ID 分离。** `session_handle` / `turn_id` 是 Knoa 主键；Codex thread/turn ID 只是内部 binding。
4. **先提交，再产生外部效果。** binding、Execution snapshot、command 和 interaction 必须在外部调用前持久化。
5. **能力显式协商。** Core 不通过 `agent_id` 猜测 steer、输入类型或事件能力。
6. **命令与事件分离。** start/steer/interrupt/resolve 是命令；内容、工具、交互请求和终态是事件。
7. **终态唯一。** 每个成功启动的 Runtime Turn 必须产生且只产生一个 terminal event。
8. **结果不明不重放。** 任何可能产生外部副作用的操作在结果不明时进入 `outcome_unknown`。
9. **真实持久 Session。** 每个 Runtime 都创建自己的真实持久 Session，并返回非空 opaque `runtime_session_ref`；Platform 不伪造、解析或替 Agent 生成该值。
10. **YAGNI。** 目前只有两个受信任实现，不支持配置下载代码、Python entry point 或任意第三方 Runtime 插件。
11. **不兼容旧数据。** 新 schema 是唯一 schema；旧数据库和旧运行目录在部署切换前删除或显式重建，不增加 legacy reader。
12. **单向依赖倒置。** Platform 通过 `AgentRuntime` 调用 Agent；Agent 不反向调用 Platform service、repository、ORM 或配置实现。Agent 私有状态由 Agent 自己的 repository 持久化。
13. **控制契约与数据传输分离。** `AgentRuntime` SPI 传递类型、引用和授权；Tool 调用使用标准 MCP，Artifact 内容读取使用标准 MCP Resources。SPI 不传 Python callback、数据库对象或裸文件描述符。
14. **内容按所有权分层。** Platform Product Artifact、workspace 文件和 Agent 私有文件是三种不同对象，不能用一个“path”字段混合表达。

### 4.1 Platform 与 Agent 的上下文边界

必须区分产品会话事实与模型工作上下文：

| 数据/策略 | 权威所有者 |
|---|---|
| 用户原始消息、附件引用、最终可见回复 | Knoa Platform |
| Session/Turn/Task/Approval/Artifact 状态 | Knoa Platform |
| 公共事件 seq、审计与保留策略 | Knoa Platform |
| Agent 私有历史、当前 Turn 选择哪些内容进入模型 | Agent Runtime |
| System/Developer Prompt 组装 | Agent Runtime |
| token budget、截断、摘要和上下文压缩算法 | Agent Runtime |
| ReAct/规划/工具反馈循环 | Agent Runtime |
| Runtime 私有 checkpoint 或外部 Thread | Agent Runtime |

Platform 向 Runtime 提供当前 Turn 的用户输入、附件引用和授权能力，而不是完整历史或提前压缩好的模型 Prompt。Runtime 通过自己的 `runtime_session_ref` 恢复 Agent 私有历史。Runtime 可以产生 `ContextCompacted` 和 token usage 等观察事件，但不能覆盖、删除或改写 Platform 保存的产品会话记录。

因此，同一个 Platform Session 若绑定 `knoa`，历史上下文和压缩由 `KnoaAgentRuntime` 完成；若绑定 `codex`，则由 Codex App Server 管理其 Thread 和 compaction。Platform 只保存产品可见消息、必要的观察元数据和恢复 binding，不尝试用一套历史或压缩算法统一两个 Agent。

## 5. 组件职责

### 5.1 AgentExecutionService

`AgentExecutionService` 是 Conversation 和 Task 共用的具体应用服务：

```python
from collections.abc import AsyncIterator
class AgentExecutionService:
    def execute_turn(
        self,
        request: "ExecuteAgentTurn",
    ) -> AsyncIterator["AgentEvent"]: ...

    async def steer_turn(
        self,
        command: "SteerAgentTurn",
    ) -> "AgentCommandResult": ...

    async def interrupt_turn(
        self,
        command: "InterruptAgentTurn",
    ) -> "AgentCommandResult": ...

    async def resolve_interaction(
        self,
        command: "ResolveAgentInteraction",
    ) -> "AgentCommandResult": ...
```

它负责：

- 从 Session binding 或 Task Execution snapshot 读取 `agent_id`；
- 获取对应 Runtime lease；
- 建立或恢复 Runtime Session；
- 把产品 Turn 输入转换为 Runtime 输入；
- 将 Runtime event 转换、持久化为 `AgentEvent`；
- 将 Runtime interaction 转换为持久 Approval/UserInput；
- 维护 command 幂等、deadline 和状态；
- 更新 ChatTurn/Task Execution snapshot 和终态；
- 断线后发起 reconciliation。

它不负责 Runtime 配置 CRUD、进程监督或具体 Codex JSON-RPC 解析，也不向公共 API 暴露 Runtime 原始 ID 或任意方法。

### 5.2 AgentManager

`AgentManager` 负责控制面和实例生命周期：

- 读取并校验 `default_agent` 与可选 Agent 配置；
- 保存受信任的 Runtime factory 映射；
- 返回 `agent_id -> AgentRuntime` 实例；
- enabled/disabled/draining/failed 状态；
- 健康检查、并发 lease、容量和 circuit breaker；
- 配置更新后的新实例切换与旧实例 drain；
- Core 关闭时有界 shutdown。

`AgentManager` 不是 Catalog。Catalog 暗示独立发现源和丰富元数据管理；当前只需要管理两个应用自带实现。

### 5.3 AgentRuntime

`AgentRuntime` 是受信任 Runtime 实现必须遵守的内部 SPI：

```python
class AgentRuntime(Protocol):
    @property
    def descriptor(self) -> "AgentDescriptor": ...

    async def create_session(
        self,
        request: "CreateRuntimeSession",
    ) -> "RuntimeSession": ...

    async def resume_session(
        self,
        request: "ResumeRuntimeSession",
    ) -> "RuntimeSession": ...

    async def start_turn(
        self,
        request: "RuntimeTurnRequest",
    ) -> "RuntimeTurn": ...

    async def steer_turn(
        self,
        command: "RuntimeSteerCommand",
    ) -> "RuntimeCommandResult": ...

    async def interrupt_turn(
        self,
        command: "RuntimeInterruptCommand",
    ) -> "RuntimeCommandResult": ...

    async def resolve_interaction(
        self,
        command: "RuntimeInteractionResolution",
    ) -> "RuntimeCommandResult": ...

    async def reconcile(
        self,
        request: "RuntimeReconcileRequest",
    ) -> "RuntimeObservedState": ...

    async def release_session(
        self,
        session: "RuntimeSession",
    ) -> None: ...

    async def delete_session(
        self,
        session: "RuntimeSession",
    ) -> None: ...

    async def health_check(self) -> "RuntimeHealth": ...

    async def drain(self, deadline: float) -> None: ...
```

接口设计说明：

- `create_session` 创建 Agent 私有的持久 Session，并返回稳定、非空的 `runtime_session_ref`。
- `resume_session` 按 `runtime_session_ref` 恢复已存在 Session；不存在、损坏或版本不兼容时返回稳定错误，不静默创建新 Session。
- `start_turn` 先返回 `RuntimeTurn` handle，再由 handle 提供该 Turn 的事件流；Codex Runtime 在内部负责从连接级多路复用流中过滤当前 Turn。
- `reconcile` 是查询观察，不承诺恢复待决连接级请求。
- `release_session` 只释放活动资源，不删除持久上下文；`delete_session` 才删除 Agent 私有 Session/checkpoint。
- `drain` 属于实例生命周期，不等于强制中断所有活动 Turn。

### 5.4 RuntimeTurn

```python
@dataclass(frozen=True)
class RuntimeTurn:
    runtime_turn_ref: str
    events: AsyncIterator["RuntimeEvent"]
```

`events` 必须满足：

- 同一个 Turn 内有序；
- 有界背压；
- 恰好一个 `TurnFinished`；
- `TurnFinished` 后不再产生事件；
- transport 异常不能伪装成正常完成；
- Runtime 未提供稳定事件 ID 时，明确标记 `source_event_id=None`，由 reconciliation 处理，不用随机 ID伪装可去重。

### 5.5 Agent Contract Package

`AgentRuntime` SPI 不能定义在 Platform 实现包中。建立独立、无基础设施依赖的 contract package，例如：

```text
packages/knoa-agent-contracts/
  runtime.py          # AgentRuntime、requests、commands、events
  types.py            # IDs、descriptor、limits、errors
```

依赖方向：

```text
knoa-platform --------> knoa-agent-contracts <-------- knoa-agent
                                                        |
                                                        `-- implements AgentRuntime

codex-agent-runtime-adapter ---------------------------> knoa-agent-contracts
       `-- implements AgentRuntime
```

Contract package 必须保持：

- 不依赖 Platform 数据库、repository、Web API、Pydantic 配置对象或进程管理实现；
- 不依赖 Knoa Agent 的 Prompt、模型 SDK 或 ReActLoop；
- 使用中立术语 `runtime`、`session`、`turn`、`event`，而非 `platform_repository`；
- 具有独立 semantic version；
- minor 版本只增加可选 capability 或可忽略字段；breaking change 提升 major；
- 对未知 capability 和事件扩展有明确拒绝或忽略规则；
- 同时支持进程内实现和未来 RPC transport，不把 Python callback/数据库连接放进 wire-safe contract。

当前只实现进程内绑定，但 contract 中的 request/event metadata 必须可序列化。这样未来将 Knoa Agent 提取为独立进程或服务时，只需增加 transport，不需要重写 Agent 业务逻辑。

Agent 使用工具和 Artifact 时，也不获得 Platform repository。Platform 在 `RuntimeTurnRequest` 中传递受限、可序列化的标准 MCP endpoint grant 和类型化 Content 引用。进程内与独立部署使用同一语义：Tool、Resource 和 Prompt discovery/call/read 都走 MCP；不为进程内 Knoa Agent保留专用 callback 快路径。

Contract package 只描述“当前 Turn 可以连接哪个受控 MCP endpoint”以及“输入内容是什么”，不描述 Platform 如何连接 Jira、如何存储 Artifact、如何执行 Approval。这样 `KnoaAgentRuntime` 可以作为独立服务运行，同时仍不依赖 Knoa Platform 实现。

## 6. Runtime 数据契约

### 6.1 AgentDescriptor 与 capability

不用不断膨胀的布尔字段集合，使用稳定 capability 标识和少量结构化限制：

```python
AgentCapability = Literal[
    "turn.steer",
    "interaction.approval",
    "interaction.user_input",
    "mcp.client",
    "input.image",
    "input.file",
    "input.audio",
    "event.reasoning_summary",
    "event.plan",
    "event.tool_lifecycle",
    "event.file_change",
    "event.usage",
    "event.context_compaction",
]


class AgentDescriptor:
    agent_id: str
    display_name: str
    implementation_version: str
    protocol_name: str
    protocol_version: str
    capabilities: frozenset[AgentCapability]
    limits: RuntimeLimits
```

基线能力不放入 capability：创建/恢复/释放/删除持久 Session、文本输入、启动和中断 Turn、输出 assistant 内容、产生明确终态、reconciliation 与健康检查属于所有 `AgentRuntime` 的必备契约。

Capability 是 Runtime 能力上界，不是授权。workspace、Tool、MCP、network 和 approval policy 仍由 Core 决定。

### 6.2 Runtime Session 与 binding

```python
class RuntimeSession:
    agent_id: str
    runtime_session_ref: str
    runtime_protocol_version: str
    binding_epoch: int
```

- Knoa：`runtime_session_ref=<Knoa Agent session id>`，指向 Knoa Agent Context Store 中的真实私有 Session。
- Codex：`runtime_session_ref=<threadId>`。
- `binding_epoch` 由 Core 分配，用于 fencing；Runtime 只能回显和校验，不能自行提升 epoch。

`runtime_session_ref` 不是伪造的兼容字段，而是所有有状态 Agent Runtime 的标准会话句柄。Platform 只把它当 opaque binding；不能解析、拼接或自行生成。

### 6.3 Runtime 请求与 Core 领域请求

Core 领域请求包含：

- principal、Knoa session/turn/task/execution ID；
- `agent_id`；
- workspace 授权结果；
- Tool capability grant；
- Approval policy；
- Artifact refs；
- client request/command ID。

Runtime SPI 请求只接收执行所需的有效快照：

- Runtime Session 与 binding epoch；
- Runtime operation ID；
- 当前 Turn 的有界类型化输入和 Artifact/Resource 引用，不包含 Platform 完整历史；
- 已批准 workspace；
- model/runtime options allowlist；
- 当前 Session/Turn 的短期 MCP capability grant；
- command deadline。

Runtime 不接收普通持久化 callback，不直接写 Conversation/Task repository，也不能基于原始客户端字段重新做 principal 授权。

### 6.4 Turn Content 与 Platform Artifact

`RuntimeTurnRequest` 不使用 `input: str + attachments: list[path]`，而使用有界 discriminated union：

```python
TurnInputPart = TextPart | ArtifactPart | ResourceLinkPart


class TextPart:
    text: str


class ArtifactRef:
    artifact_id: str          # Platform opaque ID；Agent 不解析
    name: str | None
    media_type: str
    size_bytes: int
    sha256: str


class ArtifactPart:
    artifact: ArtifactRef
    resource_uri: str         # 通过当前 Capability MCP Gateway resources/read
    presentation: Literal["image", "file", "audio"]


class ResourceLinkPart:
    uri: str                 # Gateway 投影后的 opaque MCP Resource URI
    name: str | None
    media_type: str | None
```

边界规则：

1. `ArtifactRef` 是产品 Artifact 的不可变描述，不是 Agent checkpoint，也不是主机 path。
2. `resource_uri` 是标准 MCP Resource URI。Runtime 使用同一个 session-scoped Gateway 执行 `resources/read`；Platform 在服务端重新校验 principal、Session、Artifact ownership、大小和 MIME。
3. SPI 不传 `/home/...`、`C:\\...`、S3/OSS 长期凭据或 Platform 数据库行对象。
4. 凭据只存在于 MCP grant 的 transport 层，不能进入 Prompt、Agent history、tool arguments、事件或日志。
5. Runtime 可以把 Artifact 的稳定 URI、digest 和必要摘要写入自己的私有历史；后续 Turn 使用刷新后的 Session grant 重新读取，不能持久化过期 token。
6. 图片/音频必须在明确的大小和 MIME allowlist 内完整读取；超大文本、日志和归档文件不直接塞入模型上下文，而通过 MCP Resource/Tool 做分页、搜索或 excerpt。
7. `ResourceLinkPart` 用于 Jira、Knoa 等上游 MCP Server 的业务 Resource。Platform Gateway 将 `upstream server_id + upstream URI` 映射为自己的 opaque Resource URI，避免不同 Server URI 冲突或泄漏上游连接信息。它仍是不可信数据引用，不因出现在 Turn 输入中获得 Prompt 或 Tool authority。

Platform Artifact Store 对 Gateway 暴露标准 MCP Resources，例如：

```text
knoa-artifact://{opaque_artifact_id}
```

URI scheme 是 Resource identity，不是私有 RPC。字节读取仍使用 MCP `resources/read`。Gateway 可以为大文本额外暴露标准 MCP Tool（如有界 excerpt/search）；第一期不设计另一套文件 callback 协议。

上游 Resource 同样由 Gateway 做 opaque 投影，例如：

```text
knoa-resource://{opaque_resource_id}
    -> internal mapping: jira + jira://issues/PROJECT-123
```

该映射是 Platform 内部路由表，不扩展 MCP wire protocol。Agent 只拿到标准 Resource URI，并继续用 `resources/read`。

三类文件必须保持分离：

| 类型 | 所有者 | Agent 如何使用 |
|---|---|---|
| App 上传、对话附件、Task 结果、用户可下载产物 | Platform Artifact Store | `ArtifactPart` + Gateway MCP Resource |
| 已授权 workspace 文件 | workspace/用户 | Knoa MCP Tool/Resource 或 Runtime sandbox；不得冒充 Product Artifact |
| Agent checkpoint、模型缓存、临时推理文件 | Agent Runtime | Agent 私有存储；Platform 不读取，默认不展示 |

### 6.5 Capability MCP Grant

Tool descriptor 和 Tool callback 不进入 `AgentRuntime` SPI。每个 Turn 携带一个短期、不可转授的 Gateway grant：

```python
class McpEndpointGrant:
    server_id: str
    transport: Literal["streamable_http"]
    endpoint: str
    authorization: OpaqueCredential
    expires_at: float
    scope_digest: str
    binding_epoch: int
```

规则：

- endpoint 只指向 Platform 管理的 Capability MCP Gateway，不允许 Agent 自行添加上游 MCP Server；
- token 绑定 principal、产品 Session、Runtime Session、binding epoch、workspace 和有效期；
- Agent 通过 `tools/list`、`resources/list/read`、`prompts/list/get` 发现当前有效能力；授权集合以 Gateway 服务端 policy 为准，`scope_digest` 只用于缓存失效和审计；
- grant 在 Turn 开始时刷新，不写入 Agent checkpoint；过期后失败关闭；
- MCP Prompt 只是可选择的任务模板。是否获取、如何放入模型上下文由 Agent 决定，但它永远不能覆盖 Platform policy；
- MCP Notifications 由 Platform Standard MCP Host 处理。Agent 不依赖 notification 维持 Product Task 的可靠性。

这使 Platform 同时承担两个标准角色：

```text
upstream MCP servers <- MCP Client/Host: Knoa Platform
Knoa/Codex Agents    <- MCP Server:      Platform Capability MCP Gateway
```

Gateway 是权限收口和协议代理，不把 Jira、Knoa 等业务逻辑搬进 Platform。

### 6.6 RuntimeEvent

SPI 事件采用 discriminated union，不再使用一个包含大量可空字段的 `RuntimeEventPayload`：

```python
RuntimeEvent = (
    AssistantDelta
    | ReasoningSummaryDelta
    | PlanChanged
    | ToolCallStarted
    | ToolCallProgressed
    | ToolCallFinished
    | ArtifactProduced
    | FileChangeReported
    | InteractionRequested
    | ContextCompacted
    | UsageReported
    | RuntimeWarning
    | TurnFinished
)
```

所有事件共有：

```text
source_event_id?       # Runtime 有稳定 ID 时提供
runtime_session_ref
runtime_turn_ref
occurred_at
```

`TurnFinished` 至少包含：

```text
status = completed | interrupted | failed | refused | outcome_unknown
final_output?
error_code?
usage?
transcript_delta?      # 内部提交资料，不直接作为公共事件输出
```

Core 转换后的 `AgentEvent` 才包含公共 `event_id`、`seq`、`session_handle` 和 `turn_id`。Reasoning 只允许公开摘要，不存储或转发 Runtime 隐藏推理。

`ArtifactProduced` 使用两种来源，不允许任意主机路径：

```python
RuntimeArtifact = ExistingResourceArtifact | InlineArtifact

class ExistingResourceArtifact:
    resource_uri: str
    media_type: str
    sha256: str

class InlineArtifact:
    name: str
    media_type: str
    data: bytes              # contract limit 内的小产物
    sha256: str
```

- MCP Tool 已在 Platform 创建的 Artifact 返回 `ExistingResourceArtifact`；Platform 解析当前 Gateway 下的 Resource URI，不重复复制。
- Agent 自身生成的小文件可用 `InlineArtifact`；`AgentExecutionService` 在发布公共事件前校验 hash/MIME/大小并写入 Platform Artifact Store。
- 超过 inline 上限的 Agent-native 大产物不在第一期支持；未来采用 Agent 临时内容服务的短期 pull URI，不把 Platform `ArtifactStore` callback 注入 Agent。
- Platform 成功入库后才产生用户可见 Artifact event。Runtime 私有草稿或临时文件若未显式 `ArtifactProduced`，始终不属于产品数据。

### 6.7 双向 interaction

Runtime 需要用户或 Core 响应时，产生：

```python
class InteractionRequested:
    interaction_id: str
    kind: Literal[
        "tool_approval",
        "permission_approval",
        "user_input",
        "mcp_elicitation",
    ]
    display: InteractionDisplay
    resolution_schema: ResolutionSchema
    expires_at: float | None
```

Core 必须先持久化 interaction，再通知客户端。解决命令使用：

```text
interaction_id + interaction_epoch + command_id
```

一个 interaction 只能成功解决一次。Runtime 断线后，如果底层响应通道已丢失，不得仅凭 Session resume 重发旧 resolution。

## 7. Knoa Agent

### 7.1 直接实现，不迁移旧 Runtime

`KnoaAgentRuntime` 是 Knoa Agent 的直接实现。实现时可以复用经评估仍正确的算法或组件，但不以旧 `src/knoa_platform/agent_runtime/runtime.py::AgentRuntime` 的 API、类结构、callback 或数据格式为兼容约束。

它内部组合：

- `ReActLoop`
- `ModelClient`
- `AgentContextStore`
- `McpClient`
- model/tool step
- cancellation state

所有权要求：

1. 删除 `commit_messages` 持久化 callback；Runtime 通过 `TurnFinished.transcript_delta` 返回待提交内容，由 `AgentExecutionService` 原子提交。
2. `confirmation` 与 `tool_commit` 不再作为任意 callback 塞进 context；工具发现和调用统一通过 session-scoped Capability MCP Gateway。需要审批时，Gateway 在 `tools/call` 未产生副作用前暂停调用，由 Platform 内部 ApprovalService 持久化并通知 App；Agent 只等待同一个标准 MCP call 的最终结果，不需要私有 approval RPC 或伪造 MCP 方法。
3. 事件直接使用新的 discriminated union，不保留旧 `RuntimeEventPayload` decoder。
4. Runtime operation ID 与产品 Turn ID 分离。
5. `cancel()` 演进为带 `command_id`、binding epoch 和结果状态的 `interrupt_turn()`。
6. 增加 `reconcile()`：进程内活动 Turn 可返回状态；进程重启后未完成 Turn 返回 `not_found`，由 Core 收敛为 interrupted/unknown，而不是伪造恢复。

不要求旧测试或旧用户数据继续工作。验收基于新设计的 Platform/Runtime contract、端到端场景和安全不变量。

### 7.2 Knoa Session 语义

Knoa 的长期产品会话事实由 Platform 拥有；Knoa Agent 的模型历史由 Knoa Agent Context Store 拥有。每个 Turn 开始时，Platform 只提供新的用户输入与附件引用，`KnoaAgentRuntime` 从自己的 Session 恢复历史并完成模型上下文构建。因此：

- `create_session()` 在 Knoa Agent Context Store 创建真实的 Agent Session 并返回稳定 `runtime_session_ref`；
- `resume_session()` 只恢复该 Agent Session，不访问 Platform Conversation Store；
- Knoa Agent 的 Session/Checkpoint 表属于 Agent 私有状态，不是第二份 Platform Conversation 表；
- 不需要让内置 Runtime 假装是外部 App Server；
- Platform 保存产品可见 conversation；正常恢复只需传递 opaque `runtime_session_ref`；
- 活动 Turn 的进程内执行状态与持久 Conversation Session 明确分离。

当 Agent 私有 Session 丢失或不可恢复时，Runtime 返回 `session_not_found/session_not_resumable`。Platform 不隐式创建一个空 Session。未来如果需要从 canonical conversation 重建，使用显式 `session.rebuild` capability 和新的 binding epoch；MVP 不实现自动重建。

### 7.3 Prompt 与上下文压缩

以下全部属于 Knoa Agent，而不是 Platform：

- system/developer prompt 模板与分层；
- skills、运行环境说明和工具 descriptors 如何进入模型上下文；
- history selection；
- token counting 与 budget allocation；
- 对旧历史、工具输出和长附件的摘要、裁剪与 artifact 引用；
- compaction summary/checkpoint 的生成和使用；
- 模型切换时的上下文适配；
- ReAct iteration、规划和观察信息的保留策略。

Platform 只施加不可绕过的上界和政策，例如最大输入/事件大小、允许的 workspace/Tool、隐私保留期限以及 reasoning 不得公开。Platform 不理解某个 Agent 的 prompt 模板，也不把压缩摘要当成用户原始消息。

`KnoaAgentRuntime` 把压缩 checkpoint 持久化到 Knoa Agent 自己的 `ContextCheckpointRepository`。该 repository 和 payload schema 都属于 Knoa Agent，不属于 Platform 或公共 Agent Runtime SPI。

### 7.4 压缩数据存放位置

“上下文缓存”拆成三种数据，不能混在 Conversation message 中：

| 数据 | 存放位置 | 是否必须持久化 | 所有权 |
|---|---|---|---|
| canonical conversation：原始消息、附件引用、最终回复 | Platform Conversation Store | 是 | Platform 语义与物理所有权 |
| compaction checkpoint：摘要、已覆盖消息水位、Agent 私有上下文状态 | Knoa Agent Context Store | 是 | Knoa Agent 语义与物理所有权 |
| assembled model context：本 Turn 最终 Prompt/messages | `KnoaAgentRuntime` 进程内内存 | 否 | Knoa Agent；Turn 结束即释放 |

第一阶段 Knoa Agent 与 Platform 虽在同一进程部署，但使用独立数据库文件或独立 schema owner，不写入 Platform Conversation 数据库：

```text
Knoa Agent data root
  `- context.db
       `- context_checkpoints
            runtime_session_ref
            state_version
            source_cursor
            agent_config_digest
            model_context_digest
            payload_blob
            revision
            created_at
            updated_at
```

约束：

1. `KnoaAgentRuntime` 只调用 Knoa Agent 内部的 `ContextCheckpointRepository`；不得 import 或调用 Platform 数据库、repository 或 service。
2. checkpoint 必须携带 `source_cursor + state_version + agent_config_digest`。Prompt 策略或 Agent 状态格式变化时，Runtime 可以判定失效；无法从 Agent 私有状态恢复时返回 `session_not_resumable`，不得反向读取 Platform 数据库。
3. checkpoint 更新使用 compare-and-swap；旧 Turn、旧 binding epoch 或并发执行不能覆盖新 checkpoint。
4. checkpoint 是 Agent 私有派生数据。损坏或版本不兼容不会破坏 Platform 原始会话，但当前 Runtime Session 可能不可恢复；是否显式重建由产品操作决定。
5. assembled Prompt、tokenized messages、模型请求体和隐藏 reasoning 只存在内存，不落库、不写日志。
6. checkpoint 只保存摘要与 Agent 自己的稳定引用，不直接读取 Platform Artifact repository。
7. Platform 删除 Session 时调用标准 `AgentRuntime.delete_session(runtime_session_ref)`；Knoa Agent 在该操作内级联删除自己的 checkpoint。Platform 不直接删 Agent 表。
8. Knoa Agent 自己负责 checkpoint 加密、配额和保留期；公共 API、SSE 和普通诊断接口不返回 payload。

`ContextCheckpointRepository` 是 Knoa Agent 内部 port，可以有 SQLite、文件或远程数据库实现，但不进入 `knoa-agent-contracts`。Codex 的 Thread 数据同样由 Codex App Server 自己持久化。这样两个 Agent 都对自己的上下文状态拥有完整所有权。

### 7.5 与 Codex compaction 接口的关系

Codex App Server 已提供 Runtime 自己的上下文管理接口：

- `thread/compact/start`：触发指定 Thread 的手工压缩；
- `contextCompaction` Item：通过标准 `item/started` / `item/completed` 通知压缩过程；
- `thread/read`：读取已持久化 Thread；
- `thread/resume`：恢复 Thread 并继续后续 Turn；
- `thread/delete`：删除 Thread rollout 和关联元数据。

压缩结果保存在 Codex 自己的持久 Thread/rollout 中。App Server 没有要求 Host 导出、解释或回写一个通用 compaction-summary blob。因此 Knoa 不设计跨 Runtime 的 `save_compaction()` / `load_compaction()` SPI，也不把 Codex Thread 内容复制到 Platform。

两个 Runtime 的统一语义是：

```text
Agent Runtime owns model context and compaction
Platform owns product conversation and runtime binding
```

具体持久化实现允许不同：

```text
KnoaAgentRuntime
  -> Knoa Agent ContextCheckpointRepository

CodexAgentRuntime
  -> Codex App Server Thread/rollout store
  -> Platform 只保存 threadId binding
```

对于 Knoa Agent：

- 压缩判断、摘要生成、checkpoint schema 和恢复逻辑完全由 `KnoaAgentRuntime` 处理；
- checkpoint 通过 Knoa Agent 自己的 `ContextCheckpointRepository` 保存；
- `AgentExecutionService` 不参与摘要内容，不在每个 Turn 搬运 checkpoint，也不把 checkpoint 暴露为产品消息；
- 若未来 Knoa Agent 独立部署，Context Store 随 Agent 服务一起迁移；Platform 仍只通过 `AgentRuntime` transport 调用它。

自动压缩不需要 Platform 发命令。只有产品未来需要“立即压缩”按钮或运维操作时，才增加可选 capability `session.compact` 和 `compact_session()`；当前 YAGNI，不进入基础 `AgentRuntime` SPI。Platform 可以接收统一的 `ContextCompacted` 观察事件，但该事件只包含水位、版本、token 统计和状态，不包含私有摘要正文。

### 7.6 MCP inventory 与模型工具上下文优化

“Gateway 向 Agent 暴露哪些能力”和“Agent 向模型注入哪些 Tool schema”是两层不同决策：

```text
Platform Capability Gateway
  -> 授权上界：当前 Session 最多可以使用什么

KnoaAgentRuntime
  -> 模型投影：当前 Turn 实际让模型看到什么
```

Gateway 的 `tools/list` 返回当前 grant 授权范围内的标准 MCP Tool inventory。`KnoaAgentRuntime` 不把 inventory 原样全部塞入每一次模型调用，而是在 Agent 私有边界内执行：

1. **Inventory cache**：按 `runtime_session_ref + scope_digest` 缓存标准 Tool definition、schema digest 和短描述；`scope_digest` 变化或收到标准 list-changed 信号后失效。
2. **确定性规范化**：Tool 按稳定名称排序，JSON Schema 使用确定性序列化；不把动态健康状态、时间戳、token 或随机顺序混入模型 Tool definition。
3. **会话静态核心**：普通会话必需的 built-in tools 使用确定性排序的精简调用签名静态注入，构成稳定的模型前缀。当前核心包含文件、网络、桌面、附件、记忆、天气以及会话内任务等直接用户能力；不按中英文关键词裁剪，避免“查天气”一类召回遗漏。
4. **MCP 按需激活**：Platform MCP 管理工具和上游 MCP tools 默认只存在于完整 inventory，不进入普通模型调用。模型通过常驻的 `tool_help` 发现并确认具体工具后，`KnoaAgentRuntime` 从下一次 ModelStep 起把命中的工具加入该 Runtime Session 的活动集合；后续相关 Turn 可复用，`scope_digest` 变化时重新核对授权 inventory。
5. **模型签名不是 MCP Tool**：Knoa Agent 对活动 Tool 生成私有的最小 model signature，只保留名称、参数结构、基础类型、`required`、`enum` 和必要组合结构；删除重复 description、`pattern`、长度、范围、默认值和输出 schema。该投影只用于 LLM provider 的 function-calling 字段，不对外发布，也不冒充 MCP 协议对象。
6. **按需完整帮助**：`tool_help` 返回该工具的完整 MCP description、权威 `inputSchema`、验证约束和 examples。帮助结果只进入当前工作上下文，不改变 Platform registry；它可以触发 Knoa Agent 会话级 model signature 激活。
7. **独立 schema budget**：model signature 使用独立预算。静态核心加已激活集合超限时明确失败或减少已激活 MCP 集合，不能删掉 `required`、参数形状等导致模型无法正确构造调用的信息。
8. **提交端完整校验**：无论模型看见的是精简 signature 还是完整帮助，Gateway 在每次 `tools/call` 时仍按标准 MCP inventory 对应的权威完整 schema、policy、Approval 和 binding epoch 重新校验；模型侧 schema 从来不是授权或验证依据。

这里的 `tools/list` 是标准 MCP Client 与 MCP Server 之间的发现操作，不是直接注入模型的一个 Tool。模型侧只看到 `tool_help` 和当前活动 Tool signatures。发现闭环为：

```text
MCP tools/list -> Agent inventory
                       |
                       +-> static built-in signatures
                       `-> tool_help discovery -> session activation
                                                   -> next ModelStep signature
MCP tools/call <- Gateway full-schema validation <- model tool call
```

第一期不引入 embedding Tool router、关键词自动召回或单独的 Tool recommendation 模型。built-in 核心保持静态；MCP tool 通过明确的 `tool_help` 发现与会话级激活进入模型。只有真实 inventory 规模和发现失败率证明需要时，再增加语义检索，并保留确定性的全量发现回退。

为了保留 LLM prompt/KV cache 命中，Knoa Agent 采用：

```text
稳定 system/developer instructions
  -> 稳定、规范排序的 session Tool set
  -> 稳定的压缩历史/checkpoint 投影
  -> 当前 Turn 输入、临时 Tool expansion、当前时间等易变内容
```

- 活动 Tool set 尽量按 Session/Task 保持稳定，不因每个 ReAct iteration 重新排序；
- Tool expansion 只在确实需要时发生，并在后续相关 Turn 中复用；
- 大 Tool result 不长期原样进入历史，保存为 Artifact/Resource，只在上下文中保留有界 excerpt、digest 和来源；
- 模型返回的 `cached_tokens/cache_read_input_tokens`、schema token 数、available/selected Tool 数和 selection miss 由 `KnoaAgentRuntime` 产生 `UsageReported` 观察数据；Platform 可以展示和聚合，但不参与 Tool 选择或 Prompt 排列。

真正的 KV cache payload 通常保存在 LLM Provider/推理服务；Knoa Agent 负责制造可复用的稳定前缀、选择 provider cache 参数并记录命中。它与 Knoa Agent 自己持久化的 context checkpoint 仍是两个独立机制。

## 8. CodexAgentRuntime 映射

`CodexAgentRuntime` 内部管理一个或多个受监督的 `codex app-server` 子进程，MVP 使用 stdio JSONL。

```text
AgentRuntime.create_session -> thread/start
AgentRuntime.resume_session -> thread/resume
AgentRuntime.start_turn     -> turn/start
AgentRuntime.steer_turn     -> turn/steer
AgentRuntime.interrupt_turn -> turn/interrupt
AgentRuntime.reconcile      -> thread/read + runtime status
AgentRuntime.release_session-> thread/unsubscribe
AgentRuntime.delete_session -> thread/delete
```

连接状态机：

```text
SPAWNED -> INITIALIZING -> READY -> DRAINING -> CLOSED
                         `-> FAILED
```

只有 `initialize` 成功、当前安装版本生成的 schema 可接受、所需 capability 满足后，才发送 `initialized` 并进入 READY。

事件转换示例：

```text
turn/started                    -> Runtime 内部 Turn 已接受
item/agentMessage/delta         -> AssistantDelta
item/* tool lifecycle           -> ToolCall*
item/* plan                     -> PlanChanged
审批 / user input server request -> InteractionRequested
turn/completed                  -> TurnFinished
```

输入与 Artifact 映射：

```text
TextPart                       -> turn/start text input
ArtifactPart(presentation=image)
  -> Runtime 先通过 Gateway resources/read 取回受控字节
  -> 管理的临时文件或受控 image URL
  -> Codex image/localImage input
ArtifactPart(file/audio)
  -> 若 Codex 当前 schema 原生支持则映射
  -> 否则保留为 Gateway Resource，由 Codex MCP Client 按需读取
```

`CodexAgentRuntime` 创建的 staging 文件必须位于 owner-only 的 Runtime 临时目录，校验 digest 后写入，并至少保留到对应 Turn 不再读取；不得把 Artifact Store 原始路径传给 Codex。Codex 版本不支持某种输入且又不能通过 Gateway Resource 使用时，在 `start_turn` 前返回 `capability_not_supported`，不能静默丢附件或把二进制转成 Prompt 文本。

Codex 的 MCP 配置当前由 App Server 配置文件加载并可通过 `config/mcpServer/reload` 刷新，不能假定任意 Thread 都能原子替换独立 MCP credential。MVP 因此按 principal security domain 隔离 App Server 进程，并让该进程只连接一个本地 Gateway endpoint；真正的 Session/Turn scope 由每次 Gateway 请求携带的短期 grant 校验。若所用 Codex transport 无法安全地为并发 Session 隔离 grant，则进一步收窄为每个活动 Runtime Session 一个 App Server 进程，不能共享长期 bearer token。

必须保存当前 Codex 版本生成的 schema 兼容资料：

```bash
codex app-server generate-json-schema --out <managed-schema-dir>
```

不得把 Codex 原始 JSON-RPC、Thread ID 或 Item payload 直接暴露给 App。

## 9. Agent 身份与动态配置

### 9.1 `knoa` 是否适合作为默认 Agent

适合，但必须区分两个概念：

```text
agent_id=knoa          # 稳定身份
default_agent=knoa     # 当前默认选择
```

不使用 `default` 作为 agent_id，因为默认值会变化；不使用 `native`，因为它描述部署方式而不是用户选择的 Agent；不使用 `knoa`，因为它过长且混合旧产品模块名。

如果未来出现另一个 Knoa 内置 Agent，应分配新的稳定 `agent_id`，而不是改变 `knoa` 的语义。

### 9.2 配置

最小配置：

```yaml
default_agent: knoa
```

启用 Codex：

```yaml
default_agent: knoa

agents:
  codex:
    enabled: true
    command: ["codex", "app-server"]
    config_root: "agents/codex/codex-home"
    startup_timeout_seconds: 10
    max_concurrency: 2
    workspace_policy: registered_only
    approval_policy: on_request
    sandbox_policy: workspace_write
```

规则：

- `knoa` 是应用内置实现，始终注册，不要求重复配置；
- `codex` 是应用自带但可选启用的实现；
- 配置动态，代码实现静态可信；
- 不从 URL 下载 Runtime，不从 YAML 加载 Python entry point；
- 配置更新生成 digest，只影响新 Session binding 和新 Task Execution；
- disable 先停止新 lease，再 drain 活动 Turn；
- Agent 不可用时失败关闭，不自动回退到 `knoa`。

## 10. Session 与 Task 绑定

### 10.1 Conversation Session

创建 Session 时解析一次 `agent_id`：

1. 请求显式指定时使用该值；
2. 未指定时读取当时的 `default_agent`；
3. 解析结果与配置 digest 立即持久化；
4. 同一 Session 的 Turn 不接受临时 `agent_id`；
5. 切换 Agent 创建新 Session，未来可增加显式 fork/copy-context。

创建顺序：

```text
Platform persist product Session(state=provisioning, agent snapshot)
  -> AgentRuntime.create_session(operation_id, runtime policy)
  -> persist returned runtime_session_ref
  -> product Session state=ready
```

如果 Agent Session 已创建但 Platform 在保存 binding 前崩溃，`create_session` 必须支持 operation ID reconciliation；无法确认时标记 provisioning unknown，不盲目再创建第二个 Agent Session。

建议绑定表：

```text
agent_session_bindings
----------------------
session_handle
principal_id
agent_id
agent_config_digest
runtime_session_ref
runtime_protocol_version
workspace_root
binding_epoch
owner_lease_id
owner_lease_expires_at
state
revision
created_at
updated_at
```

### 10.2 Product Task

Task Definition 保存：

```text
agent_id
agent_config_digest
```

Task Execution 保存不可变快照：

```text
agent_id_snapshot
agent_config_digest
effective_agent_config_json
runtime_session_ref
runtime_turn_ref
runtime_operation_id
binding_epoch
```

后台 Execution 创建时先调用 `create_session` 并保存返回的 `runtime_session_ref`，再启动 Turn。Execution 结束后可调用 `delete_session`；需要保留后续追问上下文时由明确 retention policy 决定，不能由 Platform 直接删除 Agent 数据目录。

规则：

- Task 未指定 Agent 时，优先继承上下文 Session，否则解析当前默认值；
- 默认值变化不影响已有 Task/Session；
- 修改 Task Agent 只影响未来 Execution；
- rerun 使用原 Execution Agent 和配置快照；
- schedule、event 和 MCP Resource Task 遵循同一规则；
- 后台 Execution 默认使用独立 Runtime Session，避免 Jira issue 等任务互相污染；
- Agent 不可用时 Execution 明确失败，不静默回退。

## 11. MCP 的位置

Agent Runtime 与 MCP 是正交维度：

```text
AgentRuntime：谁执行 Agent，如何启动、流式输出、交互、中断和恢复
MCP Server：Agent 可以发现和调用哪些 Tool、Resource、Prompt
```

Platform 内部可以有两类 capability implementation，但对 Agent 只暴露一个标准协议面：

```text
KnoaAgentRuntime MCP Client ─┐
                             ├─ Platform Capability MCP Gateway
Codex App Server MCP Client ─┘       |- Platform built-in handlers
                                      |- Artifact Resources
                                      `- upstream MCP clients
                                           |- Knoa MCP
                                           |- Jira MCP
                                           `- other MCP servers
```

- Platform control、Artifact mediation 等少量平台内聚能力可以保留为 built-in handler，但必须包装成标准 MCP Tool/Resource；
- 文件、Shell、桌面等 PC 业务能力属于 Knoa MCP Server；
- Jira 逻辑属于 Jira MCP Server；
- Agent 不持有 `ToolRegistry`、上游 Server token 或 Approval repository；它只持有 MCP Client 和当前 Turn 的 grant；
- Agent 从 `tools/list` 得到的 descriptor 如何进入 Prompt，属于 Agent 的 Prompt/context 策略；Platform 只决定哪些能力可见和可调用；
- Gateway 调用上游 Tool 前执行 policy、Approval、幂等、预算和审计。Agent 自己声称“已批准”不产生 authority。

Codex 目标路径直接使用 App Server 的 MCP Client 连接 session-scoped Gateway。`CodexAgentRuntime` 使用隔离、生成式配置，启动后通过 App Server MCP 状态接口核验只存在该 Gateway；发现用户全局 MCP、apps、plugins 或额外入口即 fail closed。

不把 App Server `dynamicTools` 作为目标架构的主路径，因为它会让 Knoa 与 Codex 拥有不同的工具协议。若当前 Codex 版本无法安全连接 scoped Gateway，则该版本声明不支持 Knoa external tools；不能降级成直连 Jira/Knoa 等上游 MCP。

OpenAI 官方 App Server 当前提供 `mcpServerStatus/list`、`mcpServer/resource/read`、`mcpServer/tool/call`，Turn 输入支持 `text`、`image` 和 `localImage`；因此 Codex adapter 可以核验 Gateway inventory，并把受控 Platform 图片映射为 URL 或 managed staging `localImage`。参考：<https://developers.openai.com/codex/app-server/>。

## 12. 命令、提交点与恢复

### 12.1 Turn 启动

```text
Core persist Turn/Execution pending + operation_id
  -> acquire agent lease
  -> resume_session(runtime_session_ref)
  -> start_turn(operation_id)
  -> persist runtime_turn_ref
  -> consume typed RuntimeEvent
  -> transactionally append AgentEvent + update snapshot
  -> consume exactly one TurnFinished
  -> commit transcript delta + terminal state
```

如果 Runtime 已创建 Turn 但 Core 在保存 `runtime_turn_ref` 前崩溃，恢复时必须使用 `operation_id` reconciliation；无法确认时进入 `outcome_unknown`，不能再创建第二个 Turn。

### 12.2 steer / interrupt

每个控制命令保存：

```text
command_id
session_handle
turn_id
agent_id
binding_epoch
command_type
payload_digest
state=requested|accepted|rejected|unknown|completed
deadline
```

Runtime 返回 `accepted/rejected/unknown`。最终 Turn 状态以 `TurnFinished` 或 reconciliation 为准。

### 12.3 interaction

Runtime 请求先持久化，再等待用户响应。Codex connection 丢失意味着 JSON-RPC request channel 丢失；`thread/resume` 不保证恢复旧 server request。因此：

- 未产生副作用的待决 interaction 可标记 `runtime_lost` 并让 Turn 失败；
- 已可能执行外部动作的请求进入 `outcome_unknown`；
- 不自动重发 approval resolution 或 dynamic tool response；
- `interaction_id + epoch` 防止旧客户端解决新请求。

### 12.4 统一错误

```text
agent_not_found
agent_disabled
agent_unavailable
agent_capacity_exhausted
runtime_protocol_error
runtime_version_unsupported
runtime_disconnected
session_not_resumable
turn_not_active
capability_not_supported
interaction_expired
workspace_denied
command_rejected
command_unknown
outcome_unknown
internal_error
```

## 13. 安全与进程模型

第一阶段：

```text
knoa Core 进程
  |- AgentExecutionService
  |- AgentManager
  |- KnoaAgentRuntime（进程内）
  `- CodexAgentRuntime
       `- codex app-server 子进程
```

Codex 子进程要求：

- bounded stdin/stdout/event queue；
- stdout JSONL 行大小限制，stderr 独立采集；
- 启动、空闲、Turn、interaction timeout；
- CPU、内存、文件描述符、进程和并发配额；
- parser deadlock 检测、kill、restart、circuit breaker；
- owner-only 的配置和凭据目录；
- principal security domain 隔离；
- 只接收已登记且已授权 workspace；
- 不向公网直接暴露 App Server WebSocket。

Capability/Artifact 额外要求：

- Gateway 只监听 loopback、Unix socket 前置代理或受保护的私网地址，不公开成为通用 MCP relay；
- grant 是短期 bearer capability，绑定 principal、Session、Runtime 和 epoch，日志必须自动脱敏；
- `resources/read` 必须重新校验 Artifact ownership、声明大小、实际大小、digest 和 MIME；
- Runtime 不允许读取 grant 范围外 URI，不跟随任意重定向，不把 signed URL/token 写入 Prompt；
- 所有 inline input/output、MCP message、Tool result 和 Artifact 均有独立上限及背压；
- Platform Artifact 删除与 Agent 私有 checkpoint 删除分别执行，互不越权。

只有 Core 和本地工作区分离的真实需求出现后，才提取 Knoa Agent Worker。若未来接远程第三方 Agent Service，可在 `AgentRuntime` 外增加传输适配，但不能让远程协议成为 Knoa 公共产品协议。

## 14. 实施顺序

### Phase 1：删除旧 Runtime 并建立新边界（已完成主路径）

- 删除旧 `AgentRuntimePort`、旧 `AgentRuntime` contract 和旧 Runtime 数据模型；
- 新建 `AgentExecutionService`、`AgentRuntime` SPI 与 `KnoaAgentRuntime`；
- 定义 `AgentRuntime` SPI、descriptor、capability 和 typed event；
- 删除 `RuntimeEventPayload` 大杂烩、transcript 持久化 callback 与 legacy decoder；
- Conversation/Task 通过 `AgentExecutionService` 调用；
- 直接创建新数据库 schema；删除旧 Session、Task、event 和 binding 数据；Knoa Agent Context Store 同步全量重建；
- 只保留与新设计一致的测试，按新 contract 重写端到端验收。
- 定义 `TurnInputPart`、`ArtifactRef`、`McpEndpointGrant` 和 Artifact output union；禁止 path/callback/repository 进入 contract。

### Phase 2：Agent 选择和持久 binding（已完成主路径）

- 增加 `AgentManager`、内置 `knoa`、可选 `codex` factory；
- 增加 `default_agent`；
- Session 保存 Agent binding；
- Task Definition/Execution 保存 Agent snapshot；
- 增加 lease、epoch、operation ID 和失败关闭语义；
- 增加管理 API：inspect、enable、disable、set-default。
- 建立 Platform Capability MCP Gateway，把现有 Platform built-in capability 与上游 MCP 统一投影为标准 MCP Tool/Resource；Knoa Agent 改为标准 MCP Client。

### Phase 3：Codex MVP（已完成首个可运行版本）

- 实现 `CodexAgentRuntime` 的 stdio JSONL 和 initialize 状态机；
- 实现 thread start/resume/read、turn start/interrupt；
- 转换 assistant/tool/artifact/terminal event；
- 使用隔离 Codex 配置并核验外部 capability inventory；
- 只连接 session-scoped Platform Capability MCP Gateway，并核验 MCP inventory；
- 实现图片 Artifact 的受控读取和 managed staging；不暴露 Artifact Store 原始 path；
- 增加 supervisor、backpressure、容量和 protocol conformance tests。

当前代码对应关系：

```text
src/knoa_platform/          Platform 控制面、产品 API 与 MCP 能力面
src/knoa_agent/             小诺 Agent 实现及其私有 checkpoint
src/knoa_agent_contracts/   中立 AgentRuntime SPI
src/knoa_codex_agent/       Codex App Server Agent 实现及私有 binding
```

已落地的 Codex 路径使用稳定 App Server API：`initialize`、
`thread/start|resume|read|delete|unsubscribe`、
`turn/start|steer|interrupt`，以及 Turn/Item/usage/interaction 通知映射。
Codex 进程通过独立 `CODEX_HOME` 启动；每个活动 Runtime Turn 的配置只挂载
Platform loopback Streamable HTTP MCP endpoint，并在启动 Turn 前通过
`mcpServerStatus/list` 核验 `knoa_platform` inventory。没有使用 experimental
`dynamicTools`。

当前尚未完成的是更强的长期进程池/崩溃重启策略，以及所有交互类型的 UI
闭环。Task Definition 与每次 Task Execution 已显式保存 Agent snapshot。
这些恢复与交互增强继续归入 Phase 4，而不是阻塞当前标准 MCP + Codex App
Server 的首个运行闭环。

### Phase 4：交互与恢复完整性

- Codex steer；
- typed approval、user input 和 MCP elicitation；
- command 状态机与 deadline；
- disconnect/restart reconciliation；
- 重复、乱序、崩溃、interaction 断线和 outcome unknown 测试。

### Phase 5：按需扩展

- Agent-native 大 Artifact 临时 pull transport；
- 本地 Agent Worker；
- 远程 Agent Service transport；
- 只有出现第三个可信 Runtime 实现后，重新评估是否需要插件 SDK。

## 15. 验收标准

1. 代码中不再存在职责含混的 `AgentRuntimePort + AgentRuntimeProvider` 双层同构接口。
2. ConversationService 和 TaskExecutor 共用 `AgentExecutionService`，不各自重复 Runtime 路由与持久化编排。
3. `KnoaAgentRuntime` 与 `CodexAgentRuntime` 直接实现同一 `AgentRuntime` SPI，没有 Native 薄适配层。
4. `knoa` 是稳定身份和初始默认值，而不是默认别名。
5. Session 创建时固化 `agent_id`，同一 Session 的 Turn 不临时换 Agent。
6. Task 每次 Execution 固化 Agent 与有效配置快照。
7. 两个 Runtime 都提供明确终态、interrupt、健康状态和 reconciliation；可选能力通过 descriptor 声明。
8. Runtime 不直接写 Conversation/Task/Approval repository，不持有产品状态权威。
9. Runtime event 为有界 discriminated union，公共事件由 Core 分配 seq 并持久化。
10. Codex Thread/Turn/Item 和 JSON-RPC 不泄漏为公共 API 主键或任意调用入口。
11. Agent 不可用时失败关闭，不自动切换到 `knoa`。
12. MCP 能力继续经过 Knoa policy、审批、幂等和审计边界。
13. Core 崩溃或 Runtime 断线后不会盲目重复创建 Turn、重复解决 interaction 或重放结果不明的外部动作。
14. Platform 持久化 canonical conversation；Knoa/Codex Runtime 分别拥有自己的 Prompt、history selection 与 context compaction 策略。
15. 新实现不存在旧 schema 回填、旧事件解码、旧 Runtime shim 或双写路径；部署切换通过清空并重建运行数据完成。
16. Platform 不提供 Agent checkpoint/content store；Knoa Agent 与 Codex 分别持久化自己的 Runtime Session 和上下文，Platform 只保存 opaque binding。
17. `KnoaAgentRuntime` 不 import Knoa Platform 实现模块，且可仅依赖中立 Agent Contract Package 和自身基础设施独立运行。
18. built-in tool 与上游 MCP tool 对所有 Agent 统一通过 session-scoped 标准 MCP Gateway 暴露；SPI 中不存在 Tool callback 或 Platform `ToolRegistry` 对象。
19. Platform Artifact 输入只以 `ArtifactRef + MCP Resource URI` 进入 Runtime，不暴露任意宿主路径、长期凭据或 repository。
20. Knoa/Codex 都能把图片输入映射到自身模型协议；不支持的文件类型在 Turn 启动前明确拒绝，不能静默丢失。
21. Tool 已创建的 Platform Artifact 通过 Resource 引用复用；Agent-native 小产物经 `ArtifactProduced(InlineArtifact)` 校验入库后才成为产品 Artifact。
22. workspace 文件、Product Artifact 和 Agent 私有文件在模型、存储、授权和清理上均有独立边界。
23. Gateway `tools/list` 定义授权上界；Knoa Agent 在自身边界内选择当前 ModelStep 的 Tool schema，不把全部 inventory 无条件注入模型。
24. 进入模型的 Tool definition 使用完整、确定性规范化的 schema；预算不足时减少 Tool 数量，Gateway 在提交端始终按权威 schema 和 policy 重新校验。
25. Knoa Agent 保留稳定 Prompt/Tool 前缀并记录 cached tokens、schema tokens 和 selected/available Tool 指标；Platform 只观察指标，不控制 Prompt cache 策略。

## 16. 最终命名

| 名称 | 含义 | 是否公共产品概念 |
|---|---|---|
| `agent_id` | 用户选择的稳定 Agent 身份 | 是 |
| `AgentExecutionService` | 路由、持久化和领域编排实现 | 否 |
| `AgentManager` | Agent 配置、实例、健康、容量和生命周期 | 管理 API 可投影状态 |
| `AgentRuntime` | 受信任 Agent 实现 SPI | 否 |
| `KnoaAgentRuntime` | 小诺内置 Agent 的直接实现 | 通过 `agent_id=knoa` 展示 |
| `CodexAgentRuntime` | Codex App Server 的直接实现 | 通过 `agent_id=codex` 展示 |

代码与发行命名固定为：

| 名称 | 边界 |
|---|---|
| `Knoa` / `knoa` | 产品名与 CLI/发行名 |
| `knoa_platform` | 产品控制面代码包 |
| `knoa_agent` | 默认小诺 Agent 实现 |
| `knoa_agent_contracts` | Platform 与 Agent 共同依赖的中立 SPI |
| `knoa_codex_agent` | Codex App Server Agent 实现 |
| `knoa_desktop_mcp` | 未来桌面能力 MCP Server 的建议名；当前并非系统名 |

此前的产品名、Python 包名、短 CLI、环境变量前缀和运行目录都不是兼容
接口；greenfield 实现不读取它们。

因此最终关系不是“Port 套 Provider”，而是：

```text
产品用例层：       AgentExecutionService
                         |
运行实现层：           AgentRuntime
                    /                \
          KnoaAgentRuntime      CodexAgentRuntime
```

这个结构既允许正向改造现有小诺 Agent，也保留 Codex App Server 所需的完整生命周期，同时没有为小诺制造一个无意义的适配壳。
