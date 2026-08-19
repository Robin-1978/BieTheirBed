# Knoa NodeAgent、Runtime 与委派设计

> 状态：权威设计。文件名保留用于历史链接；产品模型已不再包含独立 AgentProfile。
> 更新：2026-08-19

## 1. 单一 NodeAgent

过去的 `RuntimeSpec + AgentProfile = AgentDefinition` 已删除。Node 上的稳定 Agent 配置统一为：

```text
NodeAgent
├── agent_id / kind / display_name / enabled
├── instructions / instructions_ref
├── model_binding
├── default_skill_refs / allowed_skill_refs
├── allowed_tool_names / capability ceiling
├── runtime_limits
├── delegation_policy
└── codex-specific settings when kind=codex
```

合并原因不是把所有职责塞进一个巨大配置，而是这些字段共同决定一个 Node 上 Agent 的可调用身份，具有
相同生命周期、同一修改入口和同一验证边界。Skill 内容、Model、MCP、Tool 与 Secret 仍是独立资源，
NodeAgent 只保存引用或 ceiling，不拥有实例。

## 2. Runtime SPI

Runtime SPI 是统一的执行合同，不是统一 LLM：

```text
NodeAgent(kind=knoa)  -> KnoaAgentRuntime -> configured Model binding
NodeAgent(kind=codex) -> CodexAgentRuntime -> Codex managed model/config
```

Runtime 必须提供 descriptor，声明 session、interrupt、interaction、artifact 和 native capability 支持。
Composition 在启用 NodeAgent 前做 fail-closed 校验。

### 2.1 默认内置 Agent

Node 初次安装包含三个产品 Agent 配置，但只有两个 Runtime 实现：

```text
NodeAgent(knoa)           ─┐
                           ├─> built-in Knoa Runtime Worker
NodeAgent(reviewer_agent) ─┘

NodeAgent(codex)          ───> built-in Codex Runtime Adapter
```

- `knoa` 是默认面向用户的 Agent；
- `reviewer_agent` 是默认内置、默认关闭的系统 Agent，只能由 Approval Service 调用。它使用独立 Prompt、
  模型绑定、单轮限制和无 Tool 权限，但不复制一套 Runtime；
- `codex` 是默认内置、默认关闭的委派 Agent。它有不同的 Thread、模型和 native action 语义，因此使用独立
  Runtime Adapter。

“内置”表示定义随 Node Bundle 交付并受 schema 校验，不表示三个独立服务。

### 2.2 用户扩展 Agent

绝大多数用户扩展不安装执行代码。用户在目标 Node 新建另一个 `kind=knoa` 的 `NodeAgent`，配置 instructions、
模型引用、Skill 引用和 capability ceiling 即可。它仍由内置 Knoa Runtime 执行，并经 Node Configuration
草稿、preflight 与发布链热生效。产品模型不重新引入 `AgentProfile`。

只有自定义 Agent 拥有 Knoa/Codex 都无法表达的执行循环、私有 session 语义或 native integration 时，才使用
`Runtime Extension`：

```text
signed Runtime Extension Bundle
  -> Node Console / installer imports on a selected Node
  -> Node Host verifies publisher, digest, OS/arch and SPI range
  -> isolated extension Worker health check
  -> runtime kind becomes available to NodeAgent configuration
```

Runtime Extension 属于 Node-local 可执行组件，不是 Workspace 共享 Agent。Hub 可以保存目录元数据和目标 Node
的 desired deployment，但安装任意第三方代码必须由目标 Node 管理员明确批准。Extension Worker 没有自己的
Node identity、Hub enrollment、Console、长期 Hub credential 或公网入口；Platform Tool/MCP 仍只能通过
session-scoped Capability MCP Grant 使用。

## 3. ResolvedInvocationPolicy

每次 Invocation 开始时解析并冻结：

```text
ResolvedInvocationPolicy
  = NodeAgent declared ceiling
  ∩ caller/task narrowing policy
  ∩ principal/workspace policy
  ∩ Node capability availability
```

审计记录 `node_agent_digest`、Skill content digests、model identity、allowed tools、budgets 与 delegation
depth。运行中的 Invocation 不因配置修改发生权限漂移；新 Invocation 使用新 generation。

## 4. Skill、Tool 与 MCP

- Skill 是 Node 同步的共享内容，NodeAgent 保存 refs；
- Built-in Tool 是 Node-local implementation；
- MCP 是 typed external capability，经 Capability Gateway 和 Grant 校验；
- Runtime-native action 还必须受 NodeAgent ceiling 与 sandbox 限制；
- Prompt 不是安全边界。

## 5. 委派

委派只发生在同一 Node 的受管 Agent 之间，不形成跨 Node `agent_invoke`：

```text
Parent Invocation
  -> DelegationPolicy checks child_agent_id / depth / budget
  -> Child NodeAgent resolved on same Node
  -> child Invocation with narrower policy
  -> artifact/result returned to parent
```

跨 Node 工作应创建或操作目标 Node 自己的 Task/Conversation，而不是远程委派 Agent。

## 6. 配置应用

NodeAgent 配置经 ConfigurationService 的草稿、校验、preflight 和发布链应用。Prompt、Skill refs、Policy
与模型参数对新 Invocation 热生效；需要更换进程、sandbox 或 native binary 时构建新 runtime generation，
健康后切换，旧 generation 有界 drain。

## 7. 不变量

1. NodeAgent 是唯一 Agent 产品配置聚合；
2. Agent 不属于 Workspace 共享资产；
3. NodeAgent 不拥有 Skill/Model/MCP/Tool/Secret 实例；
4. 委派默认同 Node，且权限只能收窄；
5. Capability Gateway 是 Platform Tool/MCP 外部副作用的授权边界；
6. Runtime-specific 字段只在对应 kind 下出现；
7. 不建设通用跨 Node Agent sharing。
8. 普通自定义 Agent 复用内置 Runtime；只有新增执行语义才安装 Runtime Extension；
9. 第三方 Runtime 只允许 out-of-process、签名、版本化 SPI，不加载任意 Python entry point 到 Node Host。

## 8. 当前实现映射

“Profile”在本架构中是 NodeAgent 内部的配置语义，不再是一个可独立持久化、部署或授权的产品资源。完整实现
映射如下：

| 层 | 当前权威实现 |
| --- | --- |
| Runtime SPI | `knoa_agent_contracts.AgentRuntime`；Knoa 与 Codex 分别实现同一合同 |
| Agent 聚合与策略解析 | `knoa_platform.agents.definitions.NodeAgent`、`NodeAgentResolver` |
| Runtime 生命周期 | `AgentManager` 管理 active/draining generation 与并发 lease |
| Session 绑定 | `AgentSessionBindingRepository` 固化 Session 使用的 Agent identity 与 digest |
| Invocation 快照 | `InvocationPolicyRepository` 保存解析后的权限、Skill、预算、delegation depth 与 config revision |
| Subagent | `DelegationService` + `agent_delegations`，Child 使用普通 durable Task、独立 Session 和收窄后的 policy snapshot |
| Agent-facing API | `spawn_subagent` 与 `subagent(get/await/cancel)` Tool，统一经过 Capability Gateway 与 ToolStep |
| 配置持久化 | `ConfigRegistry` 保存 Draft、Revision、desired/applied state；Secret 单独进入 Node Secret Store |
| 配置 API | `/v1/config/*`；本地 Console 使用受 CSRF 保护的 `/v1/console/config*` 聚合入口 |
| 移动 App | Agent 列表与专用编辑页支持创建/删除自定义 Knoa Agent、Prompt、模型、Skill、Tool ceiling、运行限制和委派策略 |
| Node Console | 本机页面支持同一 NodeAgent 聚合的可视化修改、新建/删除、preflight 与热发布，并保留完整 JSON 高级入口 |

### 8.1 额外的 fail-closed 约束

- `default_agent` 必须是 enabled 且 `visibility=user`；
- 所有 delegation target 必须存在且 `visibility=delegate`；
- target 从 delegate 改为 user/system，管理页面会清理父 Agent 的无效引用，服务端仍再次校验；
- 内置 `knoa`、`reviewer_agent`、`codex` 不可删除，只能停用或调整角色；
- 删除自定义 Agent 不删除历史 Conversation、Task、Delegation 或 Invocation snapshot。
