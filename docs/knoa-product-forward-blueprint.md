# 小诺产品正向设计蓝图

> 状态：本轮产品与领域重构的唯一总纲
> 日期：2026-08-10
> 适用范围：Core、Secure Gateway、CLI/TUI、Android/iOS App、飞书等 Channel
> 前提：项目仍处于正向开发阶段，不兼容错误的旧 Task 产品语义。

> 本蓝图不重新定义 Task 模型。`Task → TaskExecution → ExecutionAttempt`
> 已由《Task 产品设计》和《Conversation 边界设计》确定；本蓝图的作用是统一产品闭环、
> 纠正当前实现偏离，并将既有正向设计落实到领域模型、持久化、API 和 App。

相关文档：

- 用户问题与优先级：[knoa-user-experience-audit.md](./knoa-user-experience-audit.md)
- Task 产品细节：[knoa-task-product-design.md](./knoa-task-product-design.md)
- Conversation 边界：[knoa-conversation-design.md](./knoa-conversation-design.md)
- Mobile 视觉与交互：[knoa-mobile-ui-design.md](./knoa-mobile-ui-design.md)
- 实施顺序：[knoa-product-forward-implementation-plan.md](./knoa-product-forward-implementation-plan.md)

如其他历史文档与本蓝图冲突，以本蓝图为准。

## 1. 产品原则

### 1.1 用户只理解稳定对象

用户只需要理解：

- **对话**：与小诺连续交流；
- **任务**：交给小诺独立完成的目标；
- **执行记录**：某个任务实际运行过的一次结果；
- **设置与状态**：连接、通知、能力、存储和版本。

`Session`、`Run`、`Schedule`、`Trigger`、`Occurrence`、`Attempt`、内部工具 effect/risk
等词不直接进入普通界面。

### 1.2 所有能力必须形成可恢复闭环

每项用户操作都必须同时具备：

1. 明确的开始反馈；
2. 可理解的进行状态；
3. 成功结果；
4. 失败原因；
5. 重试、修改或退出路径；
6. App 重启和断网后的恢复能力。

不能只实现后端命令而没有界面反馈，也不能只显示错误而不给下一步操作。

### 1.3 历史记录不可被当前配置改写

Task、ConversationSession 等定义可以修改；已经发生的 ChatTurn 和 TaskExecution
保存当时快照。修改只影响未来行为，不能让旧结果看起来像由新配置生成。

### 1.4 Channel 只负责展示和输入

状态机、审批原子性、执行持久化、Artifact 所有权和通知决策属于 Core。App、飞书和
其他 Channel 不通过事件顺序猜测真实状态，也不各自实现一套业务规则。

CLI/TUI 是直接连接 Core API 的本机交互客户端，不是 Channel。它与 App 一样消费
ChatTurn 快照，但不经过 Secure Gateway，也不承担外部身份映射、消息平台适配或主动
通知职责。

## 2. 顶层领域模型

```text
Principal
├── ConnectionIdentity
├── ConversationSession[]
│    └── ChatTurn[]
├── Task[]
│    ├── TaskLaunchPolicy
│    └── TaskExecution[]
│         └── ExecutionAttempt[]
├── Artifact[]
├── NotificationPreference
└── AppReleasePolicy
```

### 2.1 Conversation 与 Task 的边界

- 普通问答、连续追问、照片解释和当前上下文中的操作属于 Conversation；
- 用户明确要求后台独立完成、定时完成或在外部事件发生时完成，才创建 Task；
- ChatTurn 永不进入任务列表；
- TaskExecution 使用独立执行上下文，不修改原 ConversationSession；
- 聊天可以把有界背景显式交接给 Task，但不能隐式复制无限历史。

## 3. Task 正向模型

### 3.1 Task

Task 是稳定的用户对象，表示“小诺要做什么，以及什么时候启动”。

```text
Task
├── task_id
├── principal_id
├── title
├── goal
├── attachments[]
├── tools_enabled
├── launch_policy
├── notification_policy
├── state: active | paused | archived
├── revision
├── created_at / updated_at
└── latest_execution_id?
```

字段规则：

- `title` 用于列表，用户可编辑；创建时可由 goal 的首句生成；
- `goal` 是完整、自包含目标；
- `attachments` 是未来执行使用的稳定 Artifact 引用；
- `revision` 每次修改定义递增；
- `state=paused` 只停止未来自动启动；
- `state=archived` 从默认列表隐藏，但不删除历史；
- Task 本身没有 running/completed/failed 状态，这些属于执行记录。

### 3.2 TaskLaunchPolicy

```text
immediate
  └── 用户创建时立即执行，此后可手动再次执行

scheduled
  ├── one_time(run_at, timezone)
  ├── interval(start_at, interval_seconds, timezone)
  └── cron(expression, timezone)

event
  ├── source: webhook | jira | gitlab | file_change | ...
  ├── source_config
  └── deduplication_policy
```

Schedule、Trigger 和 Occurrence 可以作为 Core 内部实现，但公共 Task API 只返回
`launch_policy`。

### 3.3 TaskExecution

TaskExecution 是某一次实际执行，是用户可见且不可编辑的历史记录。

```text
TaskExecution
├── execution_id
├── task_id
├── task_revision
├── launch_reason: created | manual | scheduled | event | rerun
├── goal_snapshot
├── attachment_snapshots[]
├── policy_snapshot
├── state
├── phase
├── approvals[]
├── artifacts[]
├── final_result?
├── failure?
├── usage_summary
├── created_at / started_at? / finished_at?
└── trace_retention
```

状态机：

```text
queued -> running -> completed
                  -> failed
                  -> waiting_approval -> running
                  -> paused -> queued
queued/running/waiting_approval/paused -> cancelled
```

规则：

- 修改 Task 不改变已创建 Execution 的任何快照；
- 恢复继续同一 Execution；
- “再次执行”创建新 Execution，旧记录保持终态；
- 默认同一 Task 只允许一个活动 Execution；确需并发时必须显式确认；
- 运行状态、最终结果、错误、审批结果和 Artifact 引用随 Task 长期保留；
- 完整 Trace 到期后压缩，不删除最终结果。

### 3.4 ExecutionAttempt

ExecutionAttempt 只用于 lease、进程恢复和诊断：

```text
ExecutionAttempt
├── attempt_id
├── execution_id
├── ordinal
├── state
├── started_at / finished_at
└── failure_code
```

它不出现在普通 API、App、通知或飞书文案中。界面不得再把 Attempt 数显示为“执行次数”。

### 3.5 编辑、暂停、归档和删除

Task：

- 可以编辑 title、goal、附件、启动方式和通知设置；
- 修改只影响尚未创建的未来 Execution；
- 暂停 Task 只停止未来 scheduled/event 启动，不停止当前 Execution；
- 归档不删除数据，允许恢复；
- 删除 Task 时级联删除 Execution 记录；如存在活动 Execution，必须先由用户确认停止；
- 删除 Task 与停止当前 Execution 是两个明确动作，不能合并成含糊按钮。

TaskExecution：

- 不可编辑；
- 活动状态可以暂停、继续、取消；
- 终态可以“按本次配置再次执行”，生成同一 Task 下的新 Execution；
- 终态 Execution 可以从二级菜单单独删除，Task 和其他执行不受影响；
- 活动 Execution 不允许直接删除。

Task 页面上的“立即执行”使用 Task 当前 revision；Execution 页的“按本次配置再次执行”
使用该 Execution 保存的快照。两者语义必须清楚区分。

## 4. Conversation 正向体验

### 4.1 ConversationSession

- App 默认进入最近活动会话；
- “新话题”创建新 Session；
- 历史会话可以按时间查看、重命名、归档和删除；
- 新建/切换前如果有文字、附件或录音草稿，默认保存草稿，不静默丢失；
- 每个 Channel 维护自己的活动 Session，不共享短期聊天上下文。

### 4.2 ChatTurn

```text
running -> waiting_approval -> running -> completed
running -> cancelled
running -> failed
```

用户能力：

- 发送后立即看到自己的消息；
- 运行中可以停止生成；
- 失败或取消后可以重试，也可以把原输入恢复到输入框修改；
- 流中断时先恢复快照，再提示连接状态；
- 只有用户原本位于列表底部时才自动跟随新内容；
- 结果中的图片、附件和审批与正文属于同一 Turn。

## 5. Artifact 统一体验

Artifact 是聊天与任务共享的稳定资源，不按页面重复实现。

### 5.1 发送附件

每个附件独立显示：

```text
待发送 -> 上传中(进度) -> 已上传
                    -> 失败 -> 重试/移除
```

- 点击附件主体预览，独立删除按钮才执行移除；
- 最多数量、大小和类型限制在选择后立即说明；
- 一个附件失败不清空其他附件和输入文字；
- App 重启后尽可能恢复尚未发送的本地草稿。

### 5.2 接收附件

- 图片：应用内全屏查看、缩放、拖动、明确“保存到相册”和“分享”；
- 文档：显示文件名、类型、大小，可打开、保存和分享；
- 下载只写入 App 缓存，不能称为“已保存到手机”；
- 保存动作必须产生用户可见成功/失败反馈；
- 对话和执行详情复用同一预览组件。

## 6. Approval 用户模型

Approval 是持久子资源，不由 Channel 从事件序列推断。

确认卡至少展示：

- 小诺准备做什么；
- 影响对象和范围；
- 是否会写入本地、产生外部副作用或控制桌面；
- 关键参数的安全摘要；
- 是否可撤销；
- 确认和取消后的最终状态。

同一 approval_id 最多解决一次。断网、切换页面和 App 重启后必须恢复真实状态。

## 7. ConnectionIdentity 与重新配对

设备连接状态必须原子保存：

```text
ConnectionIdentity
├── gateway_url
├── device_id
├── private_key_reference
├── session_token / expiry
├── active_conversation_session
├── principal_event_cursor
├── push_registration
└── created_at / updated_at
```

规则：

- Session、Core 会话、事件游标必须按 gateway_url + device_id 隔离；
- 配对新设备或新 Gateway 时原子切换，不继承旧游标和 Core Session；
- 临时网络错误不能被误判为凭据失效并清除全部状态；
- 提供“重新连接”“重新认证”“重新配对”“移除此设备”四种不同恢复动作；
- 用户可在设置页看到当前连接对象和最近成功连接时间，不显示秘密 Token。

## 8. Notification

- 不在首次连接时无解释请求通知权限；
- 用户首次创建后台 Task 时解释用途并请求；
- 设置页展示权限、Push 注册和最近投递状态；
- 允许关闭全部通知或按 Task 配置完成、失败、待确认通知；
- 冷启动和运行期点击使用同一深链解析；
- 通知携带 task_id 和可选 execution_id，不暴露内部日志或秘密参数。

## 9. 更新策略

更新状态是全局状态，不由多个页面各自重复请求。

```text
up_to_date
optional_update
required_update
downloading
ready_to_install
```

- 可选更新使用一次全局提示；
- 强制更新进入受限模式，只允许更新、连接诊断和退出；
- 下载断点按 gateway + version_code + sha256 隔离；
- 完整且已校验 APK 的路径持久保存，App 重启后可直接继续安装；
- 安装未知来源权限仅在需要时引导，并在返回 App 后自动重试安装；
- 更新失败必须区分网络、空间不足、校验失败和安装权限问题。

## 10. 状态与设置

默认状态页回答用户问题，而不是展示内部指标：

```text
连接：已连接 / 连接中断 / 需要重新配对
小诺：可用 / 模型不可用 / 服务忙
能力：文件、网络、桌面、Skill/MCP 是否可用
通知：已启用 / 未授权 / 注册失败
存储：缓存大小与清理入口
版本：当前版本与更新状态
```

Token、模型调用数、原始工具清单、effect/risk、extension ID 和审计代码放入“高级诊断”，
并提供复制诊断信息的入口。

## 11. Mobile 信息架构

一级入口只保留：

1. 对话；
2. 任务。

设置/状态从统一入口进入。版本更新只在有更新时显示全局提示。

```text
对话
├── 当前会话
├── 新话题
└── 历史会话

任务
├── 任务列表
├── 创建/编辑任务
├── 任务详情
│    └── 执行记录列表
└── 执行详情

设置与状态
├── 连接与设备
├── 通知
├── 能力
├── 存储
├── 版本更新
└── 高级诊断
```

### 11.1 任务列表

每张卡只代表一个 Task：

```text
每天生成 Jira 摘要                         已启用
每天 09:00 · 已执行 12 次
最近一次：今天 09:03 已完成
```

筛选基于 Task 及最新 Execution：全部、进行中、待确认、未完成、已计划、已归档。
筛选必须在服务端分页之前完成。

### 11.2 任务详情

顺序固定为：

1. title、goal、附件；
2. 启动方式与下次运行时间；
3. 编辑、立即执行、暂停/启用；
4. 最新执行摘要；
5. 执行记录列表；
6. 归档和删除二级操作。

### 11.3 执行详情

顺序固定为：

1. 当前状态和阶段；
2. 最终结论或失败原因；
3. 产物；
4. 待确认操作；
5. 关键步骤；
6. 可展开完整 Trace；
7. 暂停、继续、停止或再次执行。

## 12. 公共 API 目标

### 12.1 Task

```text
GET    /v1/tasks
POST   /v1/tasks
GET    /v1/tasks/{task_id}
PATCH  /v1/tasks/{task_id}
DELETE /v1/tasks/{task_id}
POST   /v1/tasks/{task_id}/pause
POST   /v1/tasks/{task_id}/resume
POST   /v1/tasks/{task_id}/archive
POST   /v1/tasks/{task_id}/restore
POST   /v1/tasks/{task_id}/execute
GET    /v1/tasks/{task_id}/executions
```

### 12.2 TaskExecution

```text
GET    /v1/task-executions/{execution_id}
DELETE /v1/task-executions/{execution_id}
GET    /v1/task-executions/{execution_id}/stream
POST   /v1/task-executions/{execution_id}/pause
POST   /v1/task-executions/{execution_id}/resume
POST   /v1/task-executions/{execution_id}/cancel
POST   /v1/task-executions/{execution_id}/rerun
```

### 12.3 Conversation

```text
GET    /v1/conversations/sessions
POST   /v1/conversations/sessions
PATCH  /v1/conversations/sessions/{session_id}
DELETE /v1/conversations/sessions/{session_id}
GET    /v1/conversations/sessions/{session_id}/turns
POST   /v1/conversations/sessions/{session_id}/turns
GET    /v1/conversations/turns/{turn_id}
GET    /v1/conversations/turns/{turn_id}/stream
POST   /v1/conversations/turns/{turn_id}/cancel
POST   /v1/conversations/turns/{turn_id}/retry
```

### 12.4 错误响应

所有用户操作使用稳定错误结构：

```json
{
  "error": {
    "code": "connection_unavailable",
    "message": "暂时连接不上小诺",
    "retryable": true,
    "suggested_action": "reconnect",
    "correlation_id": "opaque-id"
  }
}
```

App 根据 code 映射本地化文案，不直接展示 Python 异常、HTTP 状态或内部枚举。

## 13. 持久化目标

正向表面：

```text
tasks
task_launch_policies
task_executions
task_execution_attempts
task_execution_steps
task_execution_approvals
task_execution_artifacts

conversation_sessions
conversation_turns
conversation_turn_steps
conversation_approvals

connection_identity   # App SecureStore 中的原子记录
```

旧 `runtime_tasks` 将不再承担 Task 定义和 Execution 两种职责。新实现使用清晰的新表和
模型，旧开发表不进入新查询路径；不编写旧产品语义兼容层。

## 14. 全局验收标准

- 用户列表中的一项始终是稳定 Task，而不是一次执行；
- 用户能编辑、暂停、归档和删除 Task；
- 用户能查看多次执行记录，并控制当前 Execution；
- Attempt 永不显示为执行次数；
- 修改 Task 不改变历史 Execution；
- 断网、重启和重新认证不会丢失真实状态；
- 重新配对不会继承旧 Core Session 或事件游标；
- 所有加载失败都有说明和恢复按钮；
- 对话运行中可以停止，失败后可以重试；
- 图片缓存、保存和分享语义明确；
- Approval 直接读取持久状态；
- 通知权限有解释、有设置、有冷启动导航；
- 强制更新真正限制过旧版本；
- 普通页面不暴露内部术语；
- Android Release 构建、签名、安装和升级路径通过真实设备验证。

## 15. 明确不做

- 不兼容旧 Task-as-Execution 公共语义；
- 不向用户展示 ExecutionAttempt；
- 不建立通用工作流 DAG；
- 不实现多人协作、团队权限或云同步；
- 不让 Channel 自己持久化第二套 Task/Approval 状态；
- 不为保留旧开发数据牺牲新领域模型清晰度。
