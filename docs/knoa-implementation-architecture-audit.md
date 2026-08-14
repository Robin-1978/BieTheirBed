# Knoa 实现反推架构审计

> 审计日期：2026-08-14
>
> 审计基线：`a0a8926`（`knoa-v0.2.8`）
>
> 方法：以当前代码调用链、持久化模型和测试为证据，不以既有设计文档为前提。
>
> 审计时验证：`.venv/bin/pytest -q`，780 passed，1 warning。

## 0. 2026-08-14 收敛复核

本审计记录的是修改前基线。审计确认后已完成第一轮 P1 收敛：

- Core 启动将遗留 pending HumanInteraction 原子转换为 `runtime_lost`；
- per-session Turn Lease 已移到 `AgentExecutionService`，Conversation 和 Task 共用；
- MCP Resource Bridge 不再创建 Task Definition，而是匹配现有 Event Task，向
  `TriggerService` 投递 bounded snapshot 与 content digest；
- Task `launch_policy`、Agent `create_task/task.update` 与 Mobile
  `TaskLaunchEditor` 已统一支持 `mcp:<server_id> + resource_uri[_prefix]`；
- `docs/architecture.md` 已替换为当前实现架构，不再保留旧安全边界描述。

复核基线：779 个 Python 测试收集并全量通过；Mobile TypeScript typecheck 通过，
36 个 Vitest 测试通过。下文 P1 内容保留为“发现依据”，不再代表当前缺陷状态。

## 1. 执行结论

Knoa 已经不是等待实现的 Agent Platform 设计。下列核心能力已经由代码和测试证明存在：

- Channel 与 Core 分离，CLI、TUI、飞书和 App 通过 Core API 访问同一组会话、任务、审批和交互状态。
- Platform 通过独立的 Agent Runtime SPI 接入 Knoa Agent 和 Codex Agent。
- Agent 获得的是 Turn-scoped MCP Capability Grant，Tool 调用统一进入 Capability Gateway 和 ToolStep。
- Conversation 与持久 Product Task 是两条不同的产品路径。
- Product Task 已实现 Definition、Execution、Attempt、Approval、ToolStep 和 Trace。
- 外部事件已经有通用 Webhook Trigger 和 MCP Resource Task 两种接入路径。
- Memory、Skill、Artifact 和上下文压缩已经进入 Knoa Agent 的实际运行链路。

但当前实现还不能宣称以下能力已经完整成立：

- `Policy` 还不是独立 Platform SPI，目前是 Tool 自带的 `ToolPolicy` 加 ToolStep 内的固定规则。
- HumanInteraction 虽然持久化，但服务重启后的恢复语义不完整。
- 同一 Agent Session 的 Turn 串行化没有在统一执行入口强制保证。
- 外部 Agent 能获得 Platform MCP Tools，但目前不会获得 Knoa Agent 的 Memory/Skill 上下文。
- Notification 目前主要是 Task 事件流上的飞书实现，不是通用 Notification SPI。
- Task 可以在 Core 重启后恢复治理状态，但不会无条件、无感地继续执行。
- MCP Resource 自动化尚未实现“事件匹配用户已有 Task Definition”；当前 Bridge 会为每个 Resource URI 创建新的 Task Definition。

因此下一阶段的正确方向不是增加更多架构名词，而是：

1. 修复两个跨运行时不变量：Session 并发和 HumanInteraction 恢复。
2. 收敛现有命名和文档，使其与代码一致。
3. 保留已解决真实故障模式的机制，不进行核心模型重写。

## 2. 从实现反推的当前架构

```text
App / CLI / Textual TUI / Feishu
                |
                v
       Gateway / CoreClient
                |
                v
            CoreServer
                |
       +--------+---------+
       |                  |
ConversationService    TaskService
       |                  |
       |              TaskExecutor
       +--------+---------+
                |
                v
       AgentExecutionService
                |
         Agent Runtime SPI
         /              \
   Knoa Agent         Codex Agent
         \              /
          Turn-scoped MCP Grant
                |
                v
       Capability Gateway
                |
             ToolStep
       schema / capability
       policy / approval
       execution checkpoint
                |
                v
          ToolRegistry
        /       |        \
   Built-in   MCP proxy  Platform tools
```

外部事件存在两条真实路径：

```text
Webhook
  -> TriggerService
  -> durable TriggerEvent
  -> TriggerDispatcher
  -> TaskService.execute_bound_launch

MCP Resource
  -> MCPResourceTaskBridge
  -> Resource snapshot
  -> TaskService.create_definition / execute_definition
```

第二条路径仍然由 Platform Bridge 调用 TaskService；MCP Server 本身没有直接获得 Task Repository 或 Core 状态修改权限。但它没有经过通用 `TriggerService`，因此当前架构不应声称“所有外部事件统一通过 Trigger Gateway”。

更关键的是，当前 `MCPResourceTaskConfig` 只有 Resource URI scope、principal、session、tools 和 priority，没有要激活的 `task_id`。Bridge 读取 Resource 后，把 Resource 正文拼成 goal，并调用 `create_definition()`。因此当前实现是：

```text
MCP Resource URI
  -> Bridge 生成 Task Definition
  -> Task Execution
```

而不是目标模型：

```text
MCP Resource Event
  -> Trigger matching
  -> 用户已有 Task Definition
  -> Task Execution
```

目标模型更符合 Knoa 的通用平台边界。应统一“事件触发已有 Task Definition”的语义，但不要求 MCP Resource 和 Webhook 立即共享同一张表或同一个 Adapter 实现。

## 3. 核心抽象审计

| 抽象 | 实现判断 | 代码事实 | 收敛结论 |
|---|---|---|---|
| Channel | 已验证 | `ApplicationDaemon` 独立启动 Core、Gateway、Webhook 和 `ChannelRuntime`；飞书只通过 `CoreClient` 访问 Core | 保留边界，不让 Channel 类型进入 Core |
| Agent SPI | 已验证 | `knoa_agent_contracts.AgentRuntime` 定义 Session、Turn、Steer、Interrupt、Interaction、Reconcile；Knoa/Codex 各有实现 | 这是稳定协议候选 |
| Capability Gateway | 已验证 | Agent 每个 Turn 获得短期 Grant；MCP `tools/list/call` 都按 Grant 解析；Registry 的 unchecked `_commit` 只由 ToolStep 调用 | 这是实际安全边界 |
| Policy | 部分实现 | `ToolPolicy(effect/capabilities/risk)` 由 Tool 提供；ToolStep 内固定判断 capability 和 confirmation | 不要提前包装成 Policy SPI |
| Approval | 已验证 | Task 和 Conversation 都先持久化审批，再等待任一 owner client 解析；批准后重新检查 Tool、Schema、参数和 Policy | 保留；Reviewer 不能绕过 Gateway |
| Reviewer Agent | 已验证但非权限边界 | Reviewer Agent 无 Tool；Platform 可按配置用其结果自动处理低/中风险审批 | 定义为 Platform 管控下的决策输入，不定义为权限系统 |
| Tool Commit | 机制已验证，命名过重 | 执行前记录状态、复用已知结果、阻断未知结果重放 | 下沉为 ToolStep execution checkpoint，不作为独立用户概念 |
| Product Task | 已验证 | `TaskDefinitionRecord` 保存目标和 Launch Policy；每次运行生成 `TaskExecutionRecord` | 保留产品模型 |
| Execution | 已验证但映射复杂 | Execution 的 `execution_id` 实际指向底层 `runtime_tasks.task_id`，再由 `task_executions` 关联 Definition | 暂不迁移表；先统一术语和 API 文档 |
| Attempt | 已验证 | Claim Task 时创建 Attempt；中断、失败、完成均有终态 | 保留内部模型 |
| Conversation | 已验证 | 独立 Repository、Turn、Approval、ToolStep 和实时 Hub；普通聊天不创建 Product Task | 保留与 Task 的产品区分 |
| HumanInteraction | 部分验证 | 支持 `user_input`、MCP elicitation、Schema 校验和跨客户端解析 | 补齐重启恢复和 SPI kind 一致性 |
| Trigger | 已验证但语义未收敛 | 通用 Webhook Trigger 有持久事件、去重、Lease 和 Retry；MCP Resource Bridge 当前直接创建 Task Definition | 统一为“Event 匹配已有 Task Definition”，Adapter/存储可继续分开 |
| Notification | Channel 实现 | Core 提供 principal Task event feed；飞书维护 cursor 并投递审批及终态通知 | 暂不提升为独立 SPI |
| Memory/Skill | Knoa Agent 已验证 | Platform 查询 principal-scoped Memory 和 Skill，作为 `RuntimeTurnContext` 提供给 Knoa Agent | 明确是 Knoa Agent 能力，不宣称所有 Agent 自动继承 |
| Artifact/Resource | 已验证 | Artifact 由 Platform 管理；Agent 仅通过授权 MCP Resource URI 读取 | 保留当前引用式边界 |

## 4. 三条真实执行链

### 4.1 Conversation

```text
Client.create_chat_turn
  -> ConversationService.create_turn
  -> AgentExecutionService.execute_turn
  -> Agent Runtime
  -> Capability Gateway
  -> Conversation Approval / Interaction / Tool checkpoint
  -> Conversation Repository snapshot
  -> Client subscription or polling
```

Conversation 是持久记录，但不是可自动恢复的后台工作。Core 重启时，`running` 和 `waiting_approval` Turn 会被标记为 `failed/service_restarted`，pending approval 会过期。

因此准确表述应为：

> Conversation 可跨 Client 查看和重试，但当前不承诺跨 Core 重启继续同一个 Turn。

### 4.2 Product Task

```text
TaskDefinition
  -> execute_definition
  -> runtime Task（作为 Execution）
  -> worker claim + Attempt
  -> AgentExecutionService
  -> Approval / HumanInteraction / Tool checkpoint
  -> Trace + terminal Task state
```

Core 重启时：

- 等待审批的 Task 保留 Approval，审批后进入新的 Attempt。
- 普通运行中的 Task 转为 paused/interrupted，需要显式 resume。
- 有未完成 Tool checkpoint 的 Task 转为 `outcome_unknown`，必须显式确认后才能恢复，并仍阻止相同 ToolStep 自动重放。

这证明 Task 是 durable work，但恢复策略是 fail-closed 的显式恢复，而不是自动续跑。

### 4.3 Tool 调用

```text
Agent proposed call
  -> MCP Capability Gateway
  -> Tool exists/configured
  -> JSON Schema validation
  -> call-specific ToolPolicy
  -> capability check
  -> argument normalization
  -> approval when required
  -> post-approval stale check
  -> execution checkpoint when owner is durable
  -> ToolRegistry._commit
  -> durable result checkpoint
```

当前所谓 `Tool Commit` 实际位于 ToolStep 内部，不是第二次授权，也不需要用户再操作一次。建议文档统一改称“ToolStep 执行检查点”。

## 5. 优先发现

### P1：MCP Resource Bridge 目前不是 Trigger-bound Task Definition 模型

你与其他 Agent 讨论的目标模型是正确的：MCP Server 只提供业务事实，用户创建的 Task Definition 决定自动化行为。

当前实现与它有三处实质差异：

1. `MCPResourceTaskConfig` 没有绑定已有 Task Definition。
2. `MCPResourceTaskBridge._create_task()` 使用 Resource 内容动态创建 Task Definition。
3. `_RouteState.processed` 按 URI 永久去重；同一个 mutable Resource URI 的再次 `resources/updated` 不会产生新 Execution。当前实现实际上假设每个业务事件都有一个新的、不可变 Resource URI。

这意味着 Jira Issue 使用固定 URI，例如 `jira://issue/ABC-123`，即使内容持续更新，也不符合当前 Bridge 的一次 URI 一次 Task 语义。现有测试使用的是更接近 append-only event Resource 的形式：

```text
jira://assigned-to-me/events/assignment-1
```

建议的最小目标模型：

```text
MCP Resource notification / inventory
  -> Knoa Resource Event Adapter
  -> durable normalized Event
  -> match Trigger-bound Task Definition
  -> create Task Execution
```

职责边界：

- MCP Server：业务过滤、事件标识、Resource snapshot、业务 Tool。
- Task Definition：用户 Instructions、Agent、Tool enablement、Policy、Notification。
- Knoa Trigger：来源和 URI selector 匹配、去重、创建 Execution。
- Execution：保存本次 event identity 和 bounded Resource context，不修改 Task Definition。

MCP 标准提供 Resource、`resources/updated` 和 `resources/list_changed` 通知，但没有“创建 Knoa Task”的标准协议。`Resource Event` 是 Knoa 对 MCP 通知和快照的内部规范化对象，不应伪装成新的 MCP 标准方法。

为保持 YAGNI，第一版匹配只需要：

- `source = mcp:<server_id>`
- exact URI 或 URI prefix
- 可选 event kind

不要现在实现通用表达式语言。每次投递的幂等键应至少包含 `(task_definition_id, external_event_id)`；如果 MCP Server 只能提供 mutable Resource，则还需要可信 revision 或 snapshot digest。更推荐 MCP Server 暴露 append-only、带业务 event ID 的不可变 Event Resource。

### P1：pending HumanInteraction 在服务重启后可能成为孤儿

证据：

- `HumanInteraction` 已定义 `runtime_lost` 状态。
- Repository 目前只会写入 `pending`、`resolved` 和 `expired`。
- `HumanInteractionService.close()` 只取消内存 waiter，不更新持久记录。
- Task/Conversation 的重启恢复不会同步处理 pending HumanInteraction。

结果是：UI 仍可能看到一个 pending Interaction，但原 Agent Turn 和 waiter 已经不存在。用户解析它时记录可以变为 resolved，却无法把答案送回原 Runtime，也不会自然继续原执行。

最小修复：Core 启动恢复时，将失去 live Runtime Turn 的 pending Interaction 原子更新为 `runtime_lost`；Client 禁止继续解析，并提示用户 resume/retry 后重新提问。不尝试恢复任意 Provider 的内部交互状态。

### P1：同一 Agent Session 的 Turn 串行化不在统一入口

证据：

- `ConversationService` 只在自己的 `_session_lease` 中串行 Conversation Turn。
- `TaskExecutor` 不共享该 Lease。
- `AgentExecutionService` 的 binding lock 只保护 Session binding 创建，不保护整个 Turn。
- Knoa Agent Context Store 使用 CAS 防止旧 Turn 覆盖新 checkpoint；CAS 能检测冲突，但不能保证业务顺序。

Agent 创建的独立 Task 和 MCP Resource Task 通常使用 detached/isolated Session，降低了风险；但公开 Core API 仍允许多个 Product Task 或 Conversation/Task 共享 Session。因此该不变量依赖调用方习惯，而不是 Platform 强制。

最小修复：把 per-session Turn Lease 移到 `AgentExecutionService.execute_turn()`，成为 Conversation 和 Task 共用的唯一串行边界。需要并行的独立 Task 继续通过独立 Session 实现，不增加复杂调度器。

### P1：现有总架构文档已经明显漂移

`docs/architecture.md` 仍引用当前不存在或不再权威的结构，例如旧的 `AgentRuntimePort`、`ReActLoop` 路径和 Harness safety boundary，也没有准确描述当前 Agent SPI、Capability MCP Host、Conversation/Product Task 双模型及 Reviewer Agent。

最小修复：用本审计的 As-Is 模型更新总架构文档；历史设计放到 design 文档，不继续混入“当前事实”。

### P2：Task、Execution 与底层 runtime Task 存在命名重叠

当前产品层：

```text
TaskDefinition.task_id
  -> TaskExecution.execution_id
  -> runtime_tasks.task_id
```

同时 Core API 还暴露 ad-hoc `TaskSnapshot` 和 `ProductTaskSnapshot`。实现可用，但用户和维护者很容易把 Task Definition、Execution 和 runtime Task 混为一谈。

建议只收敛语言，不做数据库迁移：

- 对用户：`Task`、`Execution`。
- 对 Core 内部：明确 `TaskDefinition`、`RuntimeExecution`。
- 将旧 `create_task/get_task` 视为 Execution-level 兼容 API，不再扩展其产品语义。

### P2：Approval 与 execution checkpoint 在 Conversation/Task 各实现一套

这不是立即重构理由，因为两者恢复策略不同：Conversation 重启失败，Task 保留审批并显式恢复。但公共不变量已经重复：identity、stale check、review annotation、结果去重。

建议先补共享契约测试，暂不抽公共基类。只有发生第二次行为漂移时，再提取小型 Repository helper。

### P2：HumanInteraction SPI 的 kind 范围不一致

- Agent Runtime Contract 允许 `tool_approval`、`permission_approval`、`user_input`、`mcp_elicitation`。
- `AgentExecutionService` 对 Agent 主动发出的 Interaction 只接受 `user_input`。
- Platform HumanInteraction Repository 接受 `user_input` 和 `mcp_elicitation`。
- Tool Approval 当前走独立 ConfirmationPort，不走 Agent Interaction event。

应明确 1.0 协议：Approval 继续由 Platform Confirmation 管理；HumanInteraction 只承诺 `user_input` 和 `mcp_elicitation`。然后要么让执行服务接受两者，要么从 Agent 主动事件契约中删除当前不支持的 kind，避免“协议写了但 Host 拒绝”。

### P2：Agent SPI 目前是执行可替换，不是上下文完全可替换

所有 Agent 都能得到 Capability MCP endpoint；只有 `agent_id == "knoa"` 时才注入 `RuntimeTurnContext` 中的 Memory 和 Skill。Codex Agent 目前得到空 Context。

这不是安全缺陷，但文档必须准确：

> Agent SPI 已实现 Runtime 和 Tool 接入可替换；Platform Memory/Skill 的跨 Agent 统一注入尚未实现。

在第二个 Agent 明确需要同类上下文之前，不新增通用 Context Plugin SPI。

### P3：Notification 尚不需要独立平台抽象

当前通用部分是 principal Task event feed 和 Task `notification_policy` 数据；真正的投递、cursor、重试和卡片属于飞书 Channel。App 也有自己的本地提醒逻辑。

这已经满足当前需求。除非出现第二种后台推送 Channel 需要复用投递状态，不要创建 Notification Service/SPI。

## 6. 已证明的安全与可靠性不变量

这些机制应保留，不能为了架构简化而删除：

1. Agent 不直接持有 Tool handler，只持有短期 MCP Grant。
2. Tool inventory 过滤和 Tool call 执行都重新按 Grant 检查。
3. Schema、call-specific Policy、Capability 和参数规范化均在执行侧完成。
4. Approval 后重新检查 Tool identity、Schema、参数和 Policy，防止批准内容漂移。
5. Reviewer Agent 没有 Tool，且其结果只能经 Platform 的 Approval 流程生效。
6. durable owner 在 Tool 执行前写检查点，已知结果可复用，未知结果禁止自动重放。
7. 外部 Trigger event 有签名、大小限制、去重、持久化、Lease 和 Retry。
8. MCP Resource 内容作为不可信 Task 输入，不能覆盖系统、Tool、Approval 或 Sandbox 规则。
9. Artifact 读取受 Session 和 Grant 中的 artifact ID 限制。
10. Memory 按 principal 隔离，敏感 key 被拒绝；Context checkpoint 使用 CAS。

## 7. YAGNI 收敛方案

### 现在做

1. 修复 HumanInteraction restart recovery。
2. 将 MCP Resource Bridge 从“创建 Task Definition”收敛为“产生规范化 Event 并激活已有 Task Definition”。
3. 在 AgentExecutionService 建立统一 per-session Turn Lease。
4. 将文档中的 `Tool Commit` 改名为 ToolStep execution checkpoint；代码可随后做无行为改名。
5. 明确 HumanInteraction 1.0 支持的 kind。
6. 更新 `docs/architecture.md`，删除已经不存在的模块和旧安全边界表述。
7. 为 P1 项增加 Resource 重复更新、跨服务恢复和 Conversation/Task 并发测试。

### 现在不做

- 不创建独立 Policy Engine/SPI。
- 不统一重写 Conversation 和 Task Repository。
- 不迁移 Task/Execution 数据表。
- 不强制 MCP Resource 与 Webhook 共用 Repository；只统一 Event -> Task Definition -> Execution 的语义和幂等契约。
- 不创建全局 Proposal/Commit 生命周期。
- 不创建 Notification SPI。
- 不要求所有 Agent 立即共享 Knoa Agent 的 Context Engine。
- 不为“未来可能分布式”引入消息队列或工作流引擎。

## 8. 收敛后的核心模型

当前代码能够支持的最小、准确架构表述是：

```text
Knoa Core 管状态、身份、持久工作和治理。
Agent Runtime 管模型上下文、推理和 Turn 生命周期。
Capability Gateway 是 Agent 行动的安全边界。
ToolStep 管验证、授权、审批和一次执行。
Task 是持久目标，Execution 是一次运行，Attempt 是一次 worker 尝试。
Conversation 是实时交互记录，不承诺跨 Core 重启续跑。
HumanInteraction 是结构化用户输入，但恢复语义仍需补齐。
MCP 提供标准 Tool/Resource/Elicitation 接入，业务 Server 可以准备业务快照。
Knoa 将外部事实规范化为 Event，由用户绑定的 Task Definition 决定执行什么工作。
Skill 指导 Knoa Agent 如何组合已存在的 Capability，不创造权限。
Channel 只负责人与 Core 的呈现和传输。
```

这套表述已经足够指导下一阶段实现，不需要再增加顶层抽象。

## 9. Knoa 核心架构原则

以下原则是对当前实现和目标自动化模型的最小概括：

> MCP：这个业务系统是什么、能做什么、怎么安全地做。
>
> Task：用户想在什么情况下完成什么工作。
>
> Agent：根据事实决定这一次具体怎么做。
>
> Platform：保证整个过程可治理、可恢复、不可越权。

进一步展开：

```text
用户定义 Task Definition
        ↓
Knoa Trigger 匹配事件
        ↓
Task Execution
        ↓
Agent Reason / Plan / Decide
        ↓
Capability Gateway
        ↓
MCP Domain Model / Rules / Data / Operations
        ↓
业务系统
        ↓
MCP Resource / notification
        ↺ 回到 Trigger
```

MCP 可以拥有领域模型、领域规则、领域数据、领域操作以及业务事件/Resource；但不拥有 Knoa 用户工作流的决定权，也不应获得 Knoa Task、Approval 或 Notification 的隐式控制权。

这里的“不能控制 Platform”必须由 Platform 强制实现：MCP Server 不应仅因返回了某个 Resource 或文本，就获得创建、取消、审批或通知 Knoa 对象的权限。标准 MCP Elicitation 可以请求领域输入，但它不等于 Knoa 的写入授权或 Platform Approval。

同时，“事件闭环”是自动化模型，而不是每次执行都必须产生下一事件：

```text
Domain Event → Workflow → Agent → Domain Capability → Result
```

只有业务系统确实产生后续 Resource/notification 时，才继续形成下一轮触发。
