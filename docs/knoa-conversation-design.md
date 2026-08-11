# 小诺 Conversation 正向设计

## 1. 设计目标

小诺的聊天是多轮 Conversation，不是 Task。一次用户提问和最终回答组成一个
`ChatTurn`；聊天中的模型推理、工具调用、权限确认和流式正文都是该 Turn 的内部
细节。只有能够脱离当前聊天独立运行、暂停、恢复、重试或由时间/事件启动的工作才
是 Task。

设计遵循以下约束：

- 高内聚：Session 负责多轮上下文，Turn 负责一轮交互，Task 负责后台工作；
- 低耦合：AgentRuntime 不知道 App、飞书、Conversation 或 Task；
- YAGNI：不实现完整 Event Sourcing，不持久化逐 token 事件，不引入通用万能
  Execution 聚合；
- 正向演进：不保留 Chat-as-Task、principal 全量聊天事件重放等旧语义。

## 2. 领域模型

```text
ConversationSession
├── principal_id
├── rolling_summary
├── summarized_through_turn
└── ChatTurn
    ├── user_message
    ├── assistant_message
    ├── state
    ├── tool_steps
    ├── approvals
    └── artifacts

Task
└── TaskExecution
    ├── state
    ├── attempts
    ├── tool_steps
    ├── approvals
    └── result

AgentRuntime
├── model loop
├── tool execution
├── approval protocol
├── cancellation
└── transient RunSignal stream
```

`ChatTurn` 和 `TaskExecution` 复用 AgentRuntime，但不共享产品生命周期或持久化
策略。ChatTurn 不出现在任务列表；TaskExecution 不污染当前聊天 Session。

### 2.1 与 AgentRuntime 的关系

三者不是彼此独立运行，而是“状态独立、执行引擎复用”。调用方向必须保持单向：

```text
Channel
  -> ConversationService
       -> ConversationSession / ChatTurn Repository
       -> SessionContextService
       -> AgentRuntime.run(RunRequest, RunContext)

Task launch
  -> TaskExecutionService
       -> Task / TaskExecution Repository
       -> AgentRuntime.run(RunRequest, RunContext)

AgentRuntime
  -> ModelProviderPort
  -> ToolRuntimePort
  -> ApprovalPort
  -> ArtifactPort
```

`ConversationSession` 和 `TaskExecution` 是状态聚合；`AgentRuntime` 是无产品状态的
执行引擎。聚合本身不调用 Runtime，由各自 Application Service 负责准备上下文、
调用 Runtime、解释信号并提交结果。

共享边界只有三个 provider-neutral 契约：

```text
RunRequest
├── principal scope
├── context messages
├── current input / attachments
└── capabilities

RunSignal                     # 临时，不作为领域历史
├── reasoning_delta
├── content_delta
├── tool_update
├── approval_required
└── artifact

RunOutcome
├── status
├── final_message
├── resulting_messages
└── usage
```

ConversationService 对 RunSignal 的解释是“更新当前 ChatTurn”；TaskExecutionService
对同一信号的解释是“更新当前后台执行”。AgentRuntime 不 import Conversation、Task、
Gateway、App 或飞书模块。

当前代码中 AgentRuntime 仍直接 load/save `RuntimeSessionRepository`，这是需要删除的
旧耦合。目标结构由 ConversationService 加载与提交聊天 Session；TaskExecutionService
管理独立执行上下文；AgentRuntime 只处理调用期间传入的上下文。

### 2.2 Conversation 调用时序

```text
ConversationService
  1. load ConversationSession summary + recent Turns
  2. create ChatTurn(running)
  3. call AgentRuntime.run(...)
  4. merge transient RunSignal into current Turn presentation
  5. persist tool/approval state when required
  6. commit final user/assistant messages
  7. compact Session if token threshold is reached
```

### 2.3 Task 调用时序

```text
TaskExecutionService
  1. claim one TaskExecution
  2. prepare isolated execution context
  3. call AgentRuntime.run(...)
  4. persist tool/approval and coarse lifecycle state
  5. commit result/artifacts
  6. notify configured Channels
```

TaskExecution 可以接收创建时显式交接的有界聊天背景，但不得读取或继续修改原聊天
Session。

## 3. ConversationSession

Session 是唯一的多轮上下文边界。它拥有：

- 有序 ChatTurn；
- 较旧对话的持久化滚动摘要；
- 摘要覆盖到的最后 Turn 序号；
- 最近若干轮完整原文；
- principal 所有权和并发串行化约束。

一次模型调用的上下文固定组装为：

```text
System Prompt
+ principal 长期记忆
+ Session rolling summary
+ 最近完整 ChatTurn
+ 当前用户输入
```

完整原始 Turn 继续归档。压缩只改变下一次模型请求使用的上下文，不删除历史。

### 3.1 持久化压缩

压缩按 token 预算触发，不按每轮固定调用：

1. 未达到软阈值时不压缩；
2. 达到阈值时保留最近完整 Turn；
3. 将更早 Turn 合并进结构化 rolling summary；
4. 摘要保留决定、用户要求、关键事实、工具结果、未完成事项和重要实体；
5. 压缩失败时继续保存完整历史，并退化为有界最近窗口，不阻断聊天；
6. 成功提交 Turn 后才允许推进摘要覆盖位置。

用户长期偏好仍写入 principal Memory，不依赖 Session 摘要跨话题传播。

## 4. ChatTurn

ChatTurn 是一次前台交互，状态为：

```text
running -> waiting_approval -> running -> completed
running -> cancelled
running -> failed
```

Turn 内部详情使用稳定结构保存，而不是追加逐 token 领域事件：

```text
TurnDetail
├── reasoning block
├── content block
├── tool step
├── approval
└── artifact
```

正文和思考的 provider chunk 是临时 `RunSignal`。Channel 在内存中合并后更新当前
卡片或页面；Turn 完成时只保存合并后的 block 和最终回答。断线恢复读取 Turn 最新
快照，不重放历史 token。

工具步骤和 Approval 需要恢复、审计或用户动作，因此立即持久化为 Turn 子资源。
Approval 使用稳定 `approval_id`，Channel 只负责展示和提交决定，不拥有审批状态机。

## 5. Task

Task 只表示独立工作：

- 用户明确要求后台完成；
- App 任务页创建；
- Agent 调用带显式 `launch` policy 的 `create_task`；
- 定时或外部事件启动。

TaskExecution 使用独立 Session，拥有暂停、恢复、取消、重试和主动通知能力。
Task 的可靠事件流只包含粗粒度生命周期和需要关注的变化，不包含 reasoning/content
chunk。

## 6. Channel 与 Session

Channel 是传输和展示边界，Session 是上下文边界，两者必须分开：

```text
App topic A     -> Session A
App topic B     -> Session B
Feishu open_id  -> Session C
CLI conversation -> Session D
```

Channel 保存外部会话到 Core Session 的绑定。Core 不包含 App/飞书分支，只接收
`principal_id + session_id + turn_id`。ChatTurn 的实时更新只返回给创建它的 Channel
绑定；飞书不得 principal-wide 消费 App ChatTurn。

后台 Task 没有聊天 Channel 所有权。其完成和 Approval 由独立通知策略决定可以投递
到 App Push、飞书或其他 Channel。

## 7. API 与流

Conversation API：

```text
POST /v1/conversations/sessions
GET  /v1/conversations/sessions/{session_id}/turns
POST /v1/conversations/sessions/{session_id}/turns
GET  /v1/conversations/turns/{turn_id}
GET  /v1/conversations/turns/{turn_id}/stream
POST /v1/conversations/turns/{turn_id}/cancel
POST /v1/conversations/approvals/{approval_id}/resolve
```

Turn stream 只服务当前 Turn，允许临时 `reasoning_delta`、`content_delta`、工具和审批
更新。重连先读取 Turn 快照，再从当前活动流继续；已完成 Turn 不重放逐 token 历史。

Task API 与 Task 事件流独立，App 聊天页不订阅 principal Task 全量事件。

## 8. 持久化边界

最小表面：

```text
conversation_sessions
conversation_turns
conversation_turn_steps
conversation_approvals
tasks / task_executions / task_attempts
```

不建立聊天 token event 表。Turn 文本以合并后的 block 或最终消息保存。实现可以使用
规范化表，但对外始终返回一个聚合 Turn 快照。

## 9. 保留与淘汰

保留策略按数据职责分层，不能给整个 Conversation 或 Execution 设置一个统一 TTL：

| 数据 | 默认保留 | 淘汰方式 |
|---|---:|---|
| 用户消息、最终回答、Session rolling summary | 直到用户删除 Session | 不自动删除；上下文压缩不删除原始聊天记录 |
| ChatTurn 合并后的 reasoning、content draft 和实时 timeline | 30 天 | 到期删除内部展示细节，只保留最终问答和关键步骤 |
| ChatTurn 工具/审批审计摘要 | 随 Session 保留 | 保留工具名、状态、时间和决定；大体积原始结果按内部细节 TTL 淘汰 |
| TaskExecution 状态、最终结果、错误、usage 汇总、关键步骤和 Artifact 引用 | 随 Task 保留 | 不因 Trace 到期而删除；由用户删除 Task 时级联清理 |
| TaskExecution 完整 ExecutionTrace | 90 天 | 到期压缩为关键步骤和统计，删除 reasoning 草稿、content draft 和大体积工具结果 |
| provider chunk、SSE 快照和订阅队列 | 仅运行期 | 终态或断开后立即释放，不写可靠事件日志 |

默认期限是产品配置，不进入 Agent Prompt。清理器只处理已经终态的数据，绝不删除
`running`、`waiting_approval`、`paused` 或尚未完成提交的数据。清理必须先把完整 Trace
压缩成稳定摘要，再在同一事务中标记 `trace_compacted_at`；重复执行清理应是幂等的。

Conversation 是用户内容，不按容量静默淘汰。数据库空间压力只允许优先清理临时
chunk、已压缩 Trace 和过期受管 Artifact；如果仍超限，应停止接收新的大体积附件并
提示用户清理，而不是删除历史消息。Artifact 字节继续服从独立的 ownership/retention
规则，聊天和执行记录只保存稳定引用。

第一阶段不实现云端冷归档、按 Task 自定义 TTL 或复杂冷热存储。需要高频自动化时，
再为该 Task 显式配置执行历史上限；不能把这一需求扩散成默认删除所有用户结果。

## 10. 不变量

- 普通聊天永远不创建 Task；
- 一个 ChatTurn 只属于一个 Session；
- 同一 Session 同时只运行一个前台 Turn；
- 成功 Turn 的最终问答必须原子写回 Session 历史；
- 新话题创建新 Session；
- App 与飞书默认使用不同 Session；
- 逐 token chunk 不进入可靠数据库事件流；
- Approval 和工具状态以子资源为真相，通知信号不是第二份状态；
- Channel 断开不破坏已持久化历史或待审批状态；
- Task 列表和通知不得包含 ChatTurn。
- 淘汰内部 Trace 不得删除用户消息、最终回答或 Task 最终结果；
- 非终态 ChatTurn/TaskExecution 永不参与自动清理。
