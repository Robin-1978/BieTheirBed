# 小诺 Task 产品设计

> 产品归属：Task Definition 与 Deployment 属于 Workspace Work；TaskExecution、ExecutionAttempt 和 AgentInvocation 在目标 WorkspaceNode 上执行，并向 Workspace 同步管理投影。顶层对象和归属以 `knoa-product-domain-architecture.md` 为准。

## 1. 唯一产品概念

用户只需要理解一个核心概念：**任务（Task）**。

Task 表示“小诺要做什么，以及什么时候启动”。普通聊天不是 Task；只有用户明确
委派独立工作、在任务页创建，或任务的定时/事件条件满足时才开始一次独立执行。

Conversation、ChatTurn、Session 上下文和 Channel 绑定见
[knoa-conversation-design.md](./knoa-conversation-design.md)。

```text
Task
  ├── Workspace 归属
  ├── 目标与附件
  ├── 启动方式
  │    ├── 立即执行
  │    ├── 定时执行
  │    └── 事件启动
  ├── 目标 Node
  └── 执行记录投影
       ├── 第一次执行
       ├── 第二次执行
       └── ...
```

产品界面不出现 Job、Trigger、Activation、Run 或 Attempt。

## 2. 专业内部模型

```text
Task                 任务定义
TaskLaunchPolicy     启动方式
Deployment(kind=task) Task Published Spec 到目标 Node 的发布关系
NodeLaunchBinding    Node 应用后的本地启动器，仅内部使用
TaskLaunch           某一次启动事件，仅内部使用
TaskExecution        某一次执行
ExecutionAttempt     执行恢复尝试，仅内部使用
```

`TaskExecution` 使用独立 Agent Session，断开 Channel 后仍继续。第一版一个
TaskExecution 只由一个 Agent 执行，不实现 Subagent。

Task Definition 不是 Node 子资源。Task 的稳定 ID、目标、启动策略和执行目录属于 Workspace；Task
类型 Deployment 将具体 Published Spec 发布到目标 Node。V1 中草稿可以不选 Node，但已发布、启用或立即执行
的 Task 必须有且只有一个目标 Node，所有 TaskExecution/Attempt 都在该 Node 上运行。

### 2.1 TaskLaunchPolicy

```text
immediate
scheduled
  ├── one_time
  ├── interval
  └── cron
event
  ├── webhook
  ├── jira
  ├── gitlab
  └── file_change
```

Schedule 不再是独立产品实体；它是 Task Published Spec 的一种启动方式。原 Trigger 术语从产品和公共
领域模型中删除，事件启动只是另一种 `TaskLaunchPolicy`。发布 Task 时，LaunchPolicy 随
Deployment 下发到目标 Node，并 materialize 为 Node-local `NodeLaunchBinding`。

### 2.2 TaskLaunch

立即点击、时间到点、外部事件到达都会在目标 Node 生成类型化 `TaskLaunch`。Node-local 启动器负责
持久去重、claim、lease 和有界重试，并创建 `TaskExecution`。Hub/Workspace 控制面只发布 Desired
State、转发手动命令和接收状态投影，不运行第二套定时器。

### 2.3 TaskExecution

每次执行具有稳定 ID、状态、阶段、权限确认、事件时间线、产物和最终结果。

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running
    running --> waiting_approval
    waiting_approval --> running
    running --> paused
    waiting_approval --> paused
    paused --> queued: 恢复
    queued --> cancelled
    running --> cancelled
    waiting_approval --> cancelled
    paused --> cancelled
    running --> completed
    running --> failed
```

“再次执行”生成新的 TaskExecution；同一次执行内部因进程恢复产生的技术尝试记录为
ExecutionAttempt，不向用户暴露。

### 2.4 ExecutionAttempt 与 placement

```text
TaskDefinition
  └── Deployment(kind=task) -> WorkspaceNode
      └── TaskExecution
          └── ExecutionAttempt[]
              └── AgentInvocation
                  └── runs_on -> WorkspaceNode
```

`ExecutionAttempt` 记录实际 lease、恢复尝试和执行 Node。`ExecutionPlacement` 是 Attempt/Invocation
上的 typed value，不创建新的通用 `NodeExecution` 状态机。V1 不实现自动跨 Node recovery，也不在
同一 TaskExecution 内改 Node；改变部署目标只影响之后创建的 Execution。

### 2.5 Resource Dependency

Task Published Spec 显式引用一个 AgentDefinition，并可声明 task-specific `required_resource_refs` 和
只收窄权限的 invocation policy。RuntimeSpec、Model、Profile、Skill 和通用 Tool Policy 由
AgentDefinition dependency closure 解析，Task 不再平行配置一套。资源可以部署在同一 Node，也可以
通过 Workspace `ResourceGrant` 调用另一 Node 上的共享服务。例如 Task 在 Node B 执行而 Jira MCP
部署在 Node A 时，发布校验至少确认：

- Node B 获准调用指定 `MCPDeployment`；
- Node A 允许远程服务且当前可达；
- Secret 仍保留在 Node A，不复制给 Node B 或 Hub；
- TaskExecution 固化实际使用的 MCP Deployment generation/digest；
- Node A 不可用时按明确策略等待或失败，不静默切换实现。

V1 不建设通用服务网格或自动依赖迁移。安全优先的 Workspace 可以限制 Task 只能使用同 Node 资源。

## 3. 创建闭环

### 3.0 Agent 工具面

Agent 只注入两个紧凑的 Task 工具，内部 Schedule 记录不进入提示词：

- `create_task(title, goal, launch, target_node?)`：按显式 launch policy 创建 Task；当前 Conversation
  的绑定 Node 可作为默认值，没有默认 Node 时必须要求用户选择；
- `task(action, task_id/execution_id)`：统一 list/get/update/pause/resume/archive/
  restore/delete/execute，以及 Execution 的暂停、恢复、取消、rerun 和删除。

`launch.kind` 支持 `immediate`、`one_time`、`interval` 和 `cron`。所有提供给 Agent
的工具名、说明、字段描述、示例和错误信息统一使用英语。`schedule_id`、`trigger_id`、
Occurrence、Launch 和 Attempt 都是 Core 内部实现名，Agent 不应向用户复述。查询和
控制必须使用公开 `task_id`，不能要求 Agent 先判断底层存储类型。
Agent 与 App 必须复用同一 Task 生命周期协调层；Task 启停、归档、恢复、修改和删除
都要同步其内部 Schedule/Trigger。Agent 查询结果返回公开 launch 配置、启动器状态和
下次执行时间，不返回内部 provider ID。Task 与终态 Execution 删除属于危险操作，
必须经过用户确认。

### 3.1 聊天委派

用户明确说“放后台做”“完成后告诉我”时，当前聊天 Agent 创建 Task：

- 立即开始的独立工作使用 `immediate`；
- 单次未来执行使用 `one_time`；
- 固定间隔或日历周期分别使用 `interval`、`cron`；
- 外部条件使用 `event`；
- 当前对话立即返回任务回执，不等待执行完成。

Agent 只有在目标完整、可以独立推进且不会扩大权限时才能主动建议或创建 Task。
普通问答不能因为模型响应慢而自动变成 Task。

### 3.2 任务页创建

任务页支持输入目标、照片、文件、启动方式和目标 Node。创建 immediate Task 后，先发布到目标 Node
再生成第一条执行记录；scheduled/event Task 必须在 Node 应用 LaunchBinding 后才能显示为“已启用”，
并允许“立即执行”。

### 3.3 执行与交付

```text
Deployment(kind=task)
   ↓
目标 Node 应用 LaunchBinding
   ↓
TaskLaunch
   ↓
TaskExecution
   ↓
独立 Agent Session
   ↓
标准事件流
   ├── App Push + 任务结果页
   ├── 飞书结果卡片
   └── CLI 查询
```

TaskExecution 可以读取 principal 级长期记忆，但不复制无限聊天全文。委派只传递
明确目标、附件和有界上下文，避免阻塞或污染当前聊天。

## 4. 状态和控制

- 暂停发生在安全边界；
- 恢复继续同一 TaskExecution；
- 取消显式改变执行状态，断线本身不取消；
- 再次执行创建新 TaskExecution，并保留历史；
- 修改 Task 启动方式只影响未来执行；
- 暂停 Task 表示停止未来定时/事件启动，不强制终止当前执行；
- 当前执行的暂停/取消是独立操作。

App 和飞书最终调用相同的 Core 命令，不在 Channel 中实现状态机。

## 5. 查询和筛选

Workspace 任务列表筛选的是 Task 及其最新执行状态，可以按部署 Node 过滤，但不能以 Node
作为 Task 所有权边界：

- 进行中：最新执行为 queued/running/paused；
- 待确认：最新执行为 waiting_approval；
- 已完成：最新执行为 completed；
- 未完成：最新执行为 failed/cancelled；
- 已计划：启动方式为 scheduled/event 且 Task 启用。

筛选必须由服务端在分页之前执行，不能先取一页内部执行记录再由客户端猜测。

### 5.1 Workspace 状态投影

目标 Node 是 TaskExecution 的写入权威，并向 Workspace Registry 同步单调序列化管理投影：Execution ID、
目标 Node、状态、进度、时间戳、待审批摘要、结果摘要、Artifact 引用和所用 generation/digest。完整 Trace、
Tool/MCP 原始载荷、Artifact bytes 与 Secret 不进入普通 Workspace 投影。

投影是最终一致的读取模型。暂停、恢复、取消、审批和再次执行命令必须路由到目标 Node；Hub 不能只改
投影来伪造操作成功。Node 离线时保留最后状态并明确标记 `stale/offline`。

## 6. 结果展示

Task 详情先展示任务目标和启动方式，再展示执行记录。执行详情顺序为：

1. 最终结论；
2. 产物和附件；
3. 关键执行步骤；
4. 可展开完整事件时间线。

流式文本必须合并后渲染；Markdown 使用完整宽度；长结果不得在 Core 或 Channel
层截断，超出单卡展示能力时附加完整 Markdown 产物。

### 6.1 执行记录与 Trace 保留

`TaskExecution` 的用户可见结果和 `ExecutionTrace` 使用不同生命周期：

- Execution 的状态、最终结论、错误、usage 汇总、关键步骤、审批结果和 Artifact
  引用随 Task 长期保存；
- 完整 Trace 默认保留 90 天，用于评估、调试和复盘；
- Trace 内按 iteration/语义段合并 reasoning 和 content draft，不保存逐 token 事件；
- Trace 到期后压缩，保留关键工具步骤、错误、耗时、token 统计和最终结果，淘汰
  reasoning 草稿、正文草稿及大体积原始工具结果；
- 运行中、暂停中、待确认的 Execution 不得淘汰；
- 删除 Task 时才级联删除其 Execution 记录，Artifact 字节按独立保留策略处理。

这样 Task 详情长期可读，评估窗口内又有足够的完整运行证据，同时不会让模型 chunk
持续撑大 principal feed 或数据库。

## 7. Channel 能力

| 能力 | App | 飞书 |
|---|---|---|
| 创建 Task | 聊天委派、任务页 | 自然语言委派 |
| 查看 Task | 列表和详情 | `/tasks`、`/task <id>` |
| 立即执行 | 按钮 | `/execute <id>` |
| 暂停/启用未来启动 | 按钮 | `/task-pause <id>`、`/task-resume <id>` |
| 暂停/恢复当前执行 | 按钮 | `/pause <execution-id>`、`/resume <execution-id>` |
| 取消当前执行 | 按钮 | `/cancel <execution-id>` |
| 再次执行 | 按钮 | `/retry <execution-id>` |
| 权限确认 | 执行内按钮 | 同一执行卡片按钮 |
| 完成通知 | Push + 结果页 | 主动结果卡片 |
