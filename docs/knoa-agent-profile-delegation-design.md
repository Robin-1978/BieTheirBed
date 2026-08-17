# Knoa NodeAgent、Runtime 与委派设计

> 状态：权威设计。文件名保留用于历史链接；产品模型已不再包含独立 AgentProfile。
> 更新：2026-08-17

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
