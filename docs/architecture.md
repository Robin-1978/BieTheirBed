# Knoa Architecture

> 状态：当前实现架构
> 更新日期：2026-08-14
> 权威顺序：运行代码与测试 > 本文 > 历史设计文档

## 1. 核心定位

Knoa 是一个多 Channel、可持久运行、可治理的 Agent Platform。

四个核心职责边界：

- MCP：描述业务系统是什么、能做什么、怎么安全地做。
- Task：描述用户想在什么情况下完成什么工作。
- Agent：根据当前事实决定这一次具体怎么做。
- Platform：保证整个过程可治理、可恢复、不可越权。

Knoa 不把业务工作流写进 Jira、GitLab 等 MCP Server，也不让 MCP
Server 直接创建、取消、审批或通知 Platform 对象。

## 2. 总体结构

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

Channel 只负责呈现和传输。App、CLI、TUI 与飞书访问同一个 Core，不各自实现
Agent、Task 或 Approval 状态机。

## 3. 核心组件

### 3.1 Core

Core 管理：

- Principal 与 Session；
- Conversation 与 ChatTurn；
- Task Definition、Execution 与 Attempt；
- Approval 与 HumanInteraction；
- Trigger、Schedule 与 Notification event feed；
- Artifact、Memory 与 Agent binding；
- Capability Grant 与 Tool execution checkpoint。

CoreServer 是状态修改入口。Client、Agent 和 MCP Server 都不能绕过 Core
Repository 或 Capability Gateway 修改平台状态。

### 3.2 Agent Runtime SPI

Agent Runtime SPI 统一 Session、Turn、事件流、中断、恢复和结构化交互。

当前接入：

- Knoa Agent：使用平台提供的 Memory、Skill、Context 与 Capability MCP。
- Codex Agent：使用相同的 Agent Runtime 接入、Capability MCP 和
  `RuntimeTurnContext`；它在自己的 runtime 内决定如何渲染、压缩和消费这些上下文。
- Reviewer Agent：受限系统 Agent，没有业务 Tool，只向 Platform 提供审批建议。

同一 Platform Session 的 Turn 在 `AgentExecutionService` 中统一串行。需要并行的
独立 Task 使用 detached/isolated Session。

### 3.3 Capability Gateway

Capability Gateway 是行动安全边界。Agent 只获得 Turn-scoped、短期、可撤销的
MCP Grant。

```text
Agent Tool Call
  -> Tool 存在性检查
  -> JSON Schema
  -> ToolPolicy
  -> Capability 检查
  -> 参数规范化
  -> Reviewer 建议（按配置）
  -> 必要时人工 Approval
  -> Approval 后重新校验
  -> ToolStep execution checkpoint
  -> Tool 执行
  -> ToolResult
```

Reviewer Agent 不是权限系统。即使 Reviewer 返回允许，最终 enforcement 仍由
Capability Gateway 完成。

`ToolStep execution checkpoint` 不是第二次人工确认。它用于记录执行前状态、复用
已知结果，并在结果未知时阻止危险重放。产品层不引入独立 `Tool Commit` 对象。

### 3.4 Capability 分类

```text
Capability
├── Built-in Tools
│   ├── 文件与搜索
│   ├── Shell
│   ├── Web
│   ├── Screenshot / GUI
│   └── ...
├── Platform Capabilities
│   ├── Task
│   ├── Memory
│   ├── Artifact
│   └── MCP lifecycle
└── MCP Tools
    ├── Jira
    ├── GitLab
    └── 其他领域系统
```

Skill 只告诉 Agent 如何组合已有 Capability，不能创造不存在的 Tool，也不能绕过
Gateway、Policy 或 Approval。

## 4. Conversation 与 Task

### 4.1 Conversation

Conversation 是实时交互记录：

```text
Conversation Session
  -> ChatTurn
  -> Agent Turn
  -> live progress / approval / interaction
  -> completed / failed / cancelled
```

Conversation 可以跨 Client 查看和重试，但当前不承诺跨 Core 重启继续同一个 Turn。
重启后遗留的运行中 Turn 会进入明确失败状态。

### 4.2 Task

Task 是持久工作：

```text
Task Definition
├── title / instructions
├── Agent
├── Capability policy
├── launch_policy
├── notification_policy
└── Executions
    └── Execution
        ├── Attempt
        ├── ToolStep
        ├── Approval
        ├── HumanInteraction
        ├── Trace
        └── Artifact
```

Task Definition 不是一次执行。Schedule、Event、手动执行和 rerun 都在同一个 Task
下创建新的 Execution。

## 5. Task 启动方式

Task 使用一个统一的 `launch_policy`：

```text
Launch Policy
├── immediate
├── scheduled
│   ├── one_time
│   ├── interval
│   └── cron
└── event
    ├── webhook
    └── mcp:<server_id>
```

Event 与 Task 的关系不是独立产品对象。Trigger binding 是 Platform 内部实现。
App、CLI、TUI 和 Agent 都编辑同一个 Task Definition。

MCP Resource Event 示例：

```json
{
  "kind": "event",
  "event_source": "mcp:jira",
  "source_config": {
    "resource_uri_prefix": "jira://assigned-to-me/events",
    "include_root": true,
    "include_descendants": false
  }
}
```

MCP Resource 的发现通过 `GET /v1/mcp/resources` 暴露给 App、CLI 和 TUI。客户端可以
从 Catalog 选择 Server、Resource 和“仅此 Resource/包含子 Resource”，也可以保留
手工 URI 作为兼容兜底。Catalog 只提供事实，不创建 Task。

MCP Event 的运行链路是：

```text
MCP Resource inventory/notification
  -> MCP Resource Bridge
  -> 已有 Task Definition 的 launch_policy 匹配
  -> Trigger binding
  -> 新建 Task Execution
```

## 6. MCP 边界与事件自动化

MCP Server 可以拥有：

- Domain Model 与领域语义；
- Domain Rules 与业务过滤；
- Domain Data；
- Domain Operations；
- Resource、Prompt、Tool 与 Elicitation；
- 有稳定 identity 的业务事件 Resource。

MCP Server 不拥有：

- 用户 Task Definition；
- Knoa Execution 生命周期；
- Platform Approval；
- Channel Notification；
- 用户自动化流程的决定权。

MCP 标准提供 Resource 与 Resource notification，但不提供通用的
“Event 创建 Knoa Task”协议。Knoa 将 Resource inventory/notification 和 bounded
snapshot 规范化为内部 Trigger event：

HumanInteraction 只表示需要用户输入的交互，当前标准 kind 为 `user_input` 和
`mcp_elicitation`。工具写入授权属于独立 Approval 流程，不伪装成
HumanInteraction。Approval API 返回 effect、risk、arguments_preview、reversible
等结构化字段；各 Channel 按自己的 locale 渲染，不由 Core 写死中文或英文句子。

```text
MCP Resource inventory / notification
  -> MCPResourceTaskBridge（协议适配）
  -> bounded snapshot + content digest
  -> 匹配现有 Task.launch_policy
  -> TriggerService.receive
  -> durable TriggerEvent
  -> TriggerDispatcher
  -> TaskService.execute_bound_launch
  -> Task Execution
```

Bridge 不创建 Task Definition。相同 snapshot digest 幂等；同 URI 内容发生变化时，
新的 digest 可以产生新事件。更推荐 MCP Server 提供 append-only、带业务 event ID
的不可变 Resource。

Resource snapshot 是不可信事件输入，不能覆盖系统提示、Capability、Approval、
workspace 或 sandbox 规则。

## 7. Approval 与 HumanInteraction

Approval 用于授权 Tool side effect。HumanInteraction 用于 Agent 或 MCP Server
请求结构化用户输入，两者不是同一协议。

HumanInteraction 1.0 支持：

- `user_input`；
- `mcp_elicitation`。

服务重启后，失去 Runtime waiter 的 pending HumanInteraction 会转换为
`runtime_lost`。旧交互不能继续 resolve，用户需要 retry/resume，让 Runtime 重新发起
交互。

## 8. Context、Memory、Skill 与 Artifact

Knoa Agent 的上下文由 Agent 自己管理，Platform 提供受权限约束的数据：

```text
Platform core/relevant memory
Platform episodic memory
Active Skill instructions
Conversation / Task input
Referenced Artifact
       -> RuntimeTurnContext
       -> Knoa Agent Context Engine
```

Provider 返回的真实 token usage 优先；本地估算只用于预算和诊断。大型日志、CI
trace、图片和文件保存在 Artifact/文件中，通过引用和搜索按需读取，不强制注入整个
模型输入。

## 9. 恢复与可靠性

- Trigger event 持久化、去重、Lease、Retry 和 dead state 由 TriggerRepository 管理。
- Task worker claim 会生成 Attempt；中断后显式 resume，而不是无条件自动续跑。
- Approval 持久化并按 principal 隔离。
- Tool execution checkpoint 阻止未知 side effect 自动重放。
- 同一 Session 的 Agent Turn 统一串行。
- HumanInteraction 重启后 fail closed 为 `runtime_lost`。
- Artifact、Memory、Task、Conversation 和 Trigger 都按 principal/session 约束访问。

## 10. YAGNI 边界

当前不引入：

- 独立 Policy Engine/SPI；
- 全局 Proposal/Commit 生命周期；
- 新 Event Repository 或消息队列；
- 通用事件表达式语言；
- Conversation/Task Repository 重写；
- Notification SPI；
- 为所有 Agent 强制统一 Context Plugin。

只有出现第二个真实消费者或现有模型无法表达的故障模式时，才增加新的顶层抽象。

## 11. 主要实现位置

- Core composition：`src/knoa_platform/agent_runtime/composition.py`
- Agent 执行边界：`src/knoa_platform/agents/execution.py`
- Conversation：`src/knoa_platform/conversation/`
- Task：`src/knoa_platform/tasks/`
- Task launch lifecycle：`src/knoa_platform/service/product_task_lifecycle.py`
- Trigger：`src/knoa_platform/automation/trigger_service.py`
- Capability Gateway：`src/knoa_platform/capabilities.py`
- ToolStep：`src/knoa_platform/agent_runtime/tool_step.py`
- HumanInteraction：`src/knoa_platform/interactions.py`
- MCP adapter：`src/knoa_platform/extensions/mcp.py`
- MCP Resource event bridge：`src/knoa_platform/extensions/mcp_resource_tasks.py`
- Knoa Agent：`src/knoa_agent/`
- Mobile App：`apps/knoa-mobile/`
