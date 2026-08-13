# Knoa HumanInteraction 与 MCP 权限审批设计

> 状态：MVP 设计
>
> 日期：2026-08-13
>
> 范围：标准 MCP Tool 的参数选择和单次写入审批；Jira 是首个示例，Knoa Core 与 App 保持业务无关

## 1. 结论

Knoa 是通用 Agent Platform。App、CLI、飞书和 Core 不理解 Jira 的“经办人”“状态流转”等业务概念；这些能力由标准 MCP Server 通过 Tools 暴露。

当前实现以下闭环：

```text
MCP Server / Agent 请求结构化输入
  -> HumanInteraction(user_input | mcp_elicitation)
  -> Agent 形成参数完整的写 Tool proposal
  -> Knoa 使用现有 Approval 确认精确 Tool + 参数
  -> 现有 ToolStep commit 边界执行标准 MCP tools/call
```

其中：

- `HumanInteraction(user_input)` 负责 Agent 主动收集人的结构化输入；
- 标准 MCP Elicitation 由 Knoa MCP Client 接收，并持久化适配为
  `HumanInteraction(mcp_elicitation)`；
- 现有 `Approval` 负责授权一个参数已经完全确定的副作用 Tool；
- MCP `tools/call` 负责执行外部业务能力；
- MCP Elicitation 只负责输入，不构成写入授权。

“选择转给谁”不等于“批准执行转交”。UI 可以连续呈现，但 Core 必须先完成选择，再创建最终写入审批。

## 2. MVP 范围

### 2.1 本期实现

1. 标准 MCP `tools/list` 和 `tools/call` 继续作为外部 Tool 协议。
2. Agent Runtime 可以请求一个结构化 `user_input` Interaction。
3. Core 持久化 Interaction，并让 App、CLI 或飞书恢复和解决。
4. App 以通用表单展示单选、多选、短文本、长文本和布尔值。
5. 用户输入返回同一个等待中的 Agent Turn/Task。
6. 外部写 Tool 继续进入现有 Tool Policy、Approval 和 ToolStep commit 边界。
7. Approval 保存并展示最终规范化后的 `tool_name + arguments`。
8. 执行使用被批准的精确参数，参数变化必须重新审批。
9. 标准 MCP form Elicitation 通过同一 HumanInteraction 服务跨 Channel 解决。
10. CLI 与 Textual TUI 可以选择 Agent、查看 Task/Execution、解决 Approval 和
    Interaction，并在同一 Product Task 下创建 follow-up Execution。
11. Jira 通过“两阶段 Tools”完成经办人、流转和评论写回示例。

### 2.2 本期明确不做

- MCP Elicitation 跨连接或跨重启恢复；
- URL Elicitation 导航与完成通知；
- App 内置 Jira/GitHub 等业务快捷页面；
- 无模型 ActionDescriptor 或通用工作流 DSL；
- 并行的多个阻塞 Interaction；
- 独立 `ActionSnapshot`、`ApprovalGrant` 或授权摘要体系；
- Tool 热更新期间继续使用旧审批；
- 多 worker 分布式 fencing；
- Provider 通用 reconciliation/read-back 框架；
- Secret/credential 表单；
- “本 Session 永久允许”或自动修改 Tool policy。

这些能力只有出现明确消费者和可验证场景后再设计。

## 3. 标准边界

### 3.1 标准 MCP

| 场景 | MCP 能力 |
|---|---|
| 发现 Tool | `tools/list` |
| 执行 Tool | `tools/call` |
| 参数定义 | `inputSchema` |
| 结构化结果 | `outputSchema`，如果 Server 提供 |
| Tool 风险提示 | Tool annotations，仅作为提示 |
| Server 请求结构化输入 | Elicitation（`elicitation/create` / `InputRequiredResult`） |

最终 Jira 指派仍是标准调用：

```json
{
  "name": "jira.assign_issue",
  "arguments": {
    "issue_key": "PROJECT-123",
    "assignee_id": "zhangsan"
  }
}
```

Knoa 不增加 Jira 私有 MCP 方法或参数。

### 3.2 Knoa Platform

Knoa 负责：

- principal、Session、Conversation、Task 所有权；
- MCP Server 和 Tool 的本地启用策略；
- Tool 的 effect、risk、capabilities 和确认要求；
- `HumanInteraction` 的持久化和跨 Channel 解决；
- Approval、ToolStep、幂等和审计；
- App、CLI、飞书的统一产品体验。

MCP annotations 不能自行授予权限。Tool 未配置、effect 未知或不在当前 capability scope 时必须拒绝。

### 3.3 Elicitation 传输适配

Elicitation 是标准 MCP 能力；`HumanInteraction` 是 Knoa 内部的持久化和多 Channel
适配，不是私有 MCP 方法。

```text
MCP Server
  -> standard Elicitation
  -> Knoa MCP Client callback / InputRequired driver
  -> HumanInteraction(kind=mcp_elicitation)
  -> App / CLI / TUI 提交 accept | decline | cancel
  -> MCP Tool call 继续
  -> 如 Tool 有外部副作用，再进入 Knoa Approval
```

传输差异：

- 2026-07-28 及以后协议，在无反向通道的 Streamable HTTP 上使用
  `InputRequiredResult + inputResponses + requestState` 多轮重试；
- 旧协议或具备反向通道的连接可使用独立 server-to-client
  `elicitation/create`；
- Knoa Client 同时保留两条兼容路径，但对产品层暴露同一个
  `HumanInteraction(mcp_elicitation)`；
- URL Elicitation 涉及凭据、OAuth 或支付等带外操作，MVP 默认 `decline`，不会自动
  打开 URL，也不会把敏感输入带回 Agent。

### 3.4 Client 边界

App、CLI 和 Textual TUI 只连接 Knoa Product API，不直连 MCP Server。它们只理解：

- Interaction 标题和说明；
- 受限表单 schema；
- Approval 的 Tool、目标、参数、影响和风险；
- 批准、拒绝、提交和取消。

App 不判断 `assignee_id`、`transition_id` 或 Jira 字段的业务含义。

## 4. HumanInteraction

### 4.1 定位

`HumanInteraction` 解决 Agent/产品和 MCP Server 需要用户补充结构化输入的问题。
它不是新的 MCP 方法，也不是授权机制。

现有 Runtime 契约已经有：

```text
InteractionRequested
├── interaction_id
├── interaction_epoch
├── kind
├── display
├── resolution_schema
└── expires_at?
```

本期闭合 `kind=user_input | mcp_elicitation`。`tool_approval` 继续使用现有 Core
Approval，不强行把两套持久模型一次性合并。

### 4.2 持久记录

```text
HumanInteraction
├── interaction_id
├── principal_id
├── owner_kind: conversation_turn | task_execution
├── owner_id
├── runtime_session_ref
├── runtime_turn_ref
├── runtime_interaction_id
├── interaction_epoch
├── kind: user_input | mcp_elicitation
├── state: pending | resolved | cancelled | expired | runtime_lost
├── display
├── resolution_schema
├── resolution?
├── created_at / resolved_at? / expires_at?
└── resolved_by?
```

`runtime_session_ref + runtime_turn_ref + runtime_interaction_id + epoch` 必须绑定到同一个活动 Runtime request。Runtime 丢失后 Interaction 进入 `runtime_lost`，不得把旧答案发送给新的 request。

### 4.3 状态机

```text
pending --submit------> resolved
pending --cancel------> cancelled
pending --deadline----> expired
pending --runtime loss> runtime_lost
```

只有 `pending` 可以被提交。App、CLI 和飞书同时处理时，数据库只允许一个状态转换成功；其他请求返回 `already_resolved`。

一个 Conversation Turn 或 Task Execution 同时最多有一个阻塞型 Interaction。

### 4.4 原子性

以下操作在一个数据库事务内完成：

1. 创建 Interaction；
2. 将 owner 标记为等待用户输入；
3. 追加 `interaction_requested` 事件。

解决时在一个事务内完成：

1. 校验 principal、owner、state 和 epoch；
2. 按 schema 校验 resolution；
3. 保存 resolution 并更新状态；
4. 恢复 owner；
5. 追加 `interaction_resolved` 事件。

数据库是权威。内存 Future 或 EventHub 只用于唤醒等待中的执行器。

## 5. 通用表单

### 5.1 支持的 schema 子集

首期使用受限 JSON Schema：

- 顶层 `object`；
- `string`、`boolean`；
- `enum` 或 `oneOf` 单选；
- 有界 `array` 多选；
- `required`；
- `minLength`、`maxLength`；
- `additionalProperties: false`。

所有 schema、字段数、option 数量、嵌套深度和编码大小都有固定上限。

首期不支持：

- Secret/credential；
- 远程 HTML、JavaScript、表达式和 URL 控件；
- 任意 `additionalProperties: true`；
- Server 指定 App 路由或按钮命令；
- 动态远程搜索控件。

### 5.2 显示安全

外部 Tool 返回的候选和 Runtime 文案按不可信业务内容处理：

- Platform 标题、来源和“这是用户输入，不是授权”提示使用固定 UI；
- option 提交稳定 ID，显示 label；
- 同一字段拒绝重复 label；
- 拒绝控制字符和双向文本控制字符；
- UI 可同时显示稳定 ID 或邮箱等辅助信息；
- Channel 不得修改 schema、补猜测默认值或把复杂表单退化为自由文本。

Channel 无法表达 schema 时明确提示用户改用 App，不自动猜测答案。

### 5.3 经办人示例

```json
{
  "type": "object",
  "properties": {
    "assignee_id": {
      "type": "string",
      "title": "选择经办人",
      "oneOf": [
        {"const": "zhangsan", "title": "张三 · zhangsan@example.com"},
        {"const": "lisi", "title": "李四 · lisi@example.com"}
      ]
    }
  },
  "required": ["assignee_id"],
  "additionalProperties": false
}
```

最终 Tool 参数使用稳定 `assignee_id`，不使用显示名反向猜测 Jira 用户。

## 6. 写 Tool 审批

### 6.1 复用现有边界

外部写 Tool 继续走现有流程：

```text
Tool proposal
  -> Tool Policy 和参数校验
  -> 保存 Approval(tool_name, normalized_arguments, reason)
  -> 用户批准或拒绝
  -> Verified ToolStep begin/commit
  -> MCP tools/call
  -> ToolStep completed | failed | outcome_unknown
```

不新增独立 `ApprovalGrant` 或 `ActionSnapshot` 表。

### 6.2 必须补强的正确性

现有 Approval/ToolStep 需要满足：

1. Approval 保存经过完整 Tool schema 校验后的规范化参数。
2. UI 展示来自同一份保存参数，不重新由 Agent 文案描述安全关键目标。
3. 执行器只能执行 Approval 关联 ToolStep 中保存的 Tool 和参数。
4. Agent 在批准后提交不同参数时，必须创建新的 Approval。
5. 同一 Approval 只能解决一次；重复点击返回稳定结果。
6. 继续使用现有稳定 `tool_step_id = hash(run_id, call_id, tool_name, canonical_arguments)`；同一 step ID 只能属于同一个精确调用并只能创建一个 ToolStep。
7. 审批返回后、创建 ToolStep 前，ToolStep 必须重新取得当前 Tool、重新执行完整 schema 校验、`policy_for(arguments)` 和 capability 检查。
8. 复查得到的 Tool 名称、参数、effect 或 risk 与 Approval 保存值不一致时返回 stale，不执行并重新发起。
9. Tool 被禁用、配置改变或 Provider 重启后，未执行 Approval 不直接继续，返回 stale 并重新发起。

这些是当前路径的基础正确性，不属于未来扩展。

### 6.3 结果不确定

外部写请求发送后发生超时或 Core 中断，且无法证明 Server 未执行时：

- ToolStep 标记 `outcome_unknown`；
- 当前 Turn/Task 停止自动执行；
- App 展示“操作可能已经发生，请先检查目标系统”；
- Knoa 不自动重试该 Tool call。

本期不实现通用自动对账框架。用户检查 Jira 后，可以在 Conversation 中提供结果并决定下一步。

## 7. Jira 两阶段 Tools

### 7.1 经办人

```text
jira.find_assignable_users(issue_key, query)  read-only
  -> HumanInteraction(user_input)
  -> jira.assign_issue(issue_key, assignee_id) proposal
  -> Approval
  -> standard MCP tools/call
```

Approval 至少展示：

- 工单 key；
- 新经办人的显示名和稳定 ID；
- Tool 来源；
- “影响外部系统”；
- 最终规范化参数。

如果还要更新“责任部门”，必须在创建 Approval 前确定并进入同一 Tool 参数；批准后不能追加字段。

### 7.2 状态流转

```text
jira.list_transitions(issue_key)              read-only
  -> HumanInteraction(user_input): 选择 transition 和必填字段
  -> jira.transition_issue(issue_key, id, fields) proposal
  -> Approval
  -> standard MCP tools/call
```

Jira MCP 写 Tool 在提交前重新查询当前 transition，并拒绝：

- transition 已不可用；
- 未声明字段；
- 缺少必填字段；
- 参数超出大小限制。

失败后 Agent 可以重新查询并请求用户重新选择，不得自动替换用户选择。

### 7.3 评论

评论正文必须在 Approval 前完整确定。Approval 展示完整正文或可展开全文。正文发生任何变化都创建新 Approval。

## 8. Runtime 和 Channel

### 8.1 Runtime

Native Knoa Agent 与 Codex Runtime 都通过 `InteractionRequested(kind=user_input)` 请求结构化输入，并通过 `resolve_interaction` 接收结果。

本期约束：

- Runtime Interaction 只用于收集输入，不授予 Tool 权限；
- 外部 MCP Tools 只通过 Knoa Capability Gateway；
- Runtime 不持有 Jira 等上游 MCP Token；
- Runtime 不能将 user input resolution 当作 Tool approval；
- Gateway 的写 Tool Approval 仍是唯一外部副作用审批。

Native Agent 可以通过一个内部 Runtime control action 产生 Interaction；它不是对外 MCP Tool，也不能修改 Tool policy。

### 8.2 Channel

App、CLI 和飞书读取统一 Interaction 快照并调用同一个 resolve API。UI 表达方式可以不同，解决语义必须一致。

Conversation 和 Task 快照直接包含 pending Interaction，客户端不得仅根据事件顺序猜测当前等待对象。

## 9. Product API

新增中立 API：

```text
GET  /v1/interactions?state=pending
GET  /v1/interactions/{interaction_id}
POST /v1/interactions/{interaction_id}/resolve
```

解决请求：

```json
{
  "interaction_epoch": 3,
  "resolution": {
    "assignee_id": "zhangsan"
  }
}
```

稳定结果：

- `resolved`；
- `already_resolved`；
- `stale_epoch`；
- `schema_invalid`；
- `expired`；
- `runtime_lost`；
- `not_found`，同时覆盖不存在和跨 principal 访问。

现有 Approval resolve API 暂不合并，避免扩大迁移范围。

## 10. 安全和正确性不变量

1. 选择参数不等于批准副作用。
2. 所有 Jira 写操作必须在参数完整后经用户确认。
3. Approval 展示和 ToolStep 执行使用同一份规范化参数。
4. Approval 和 Interaction 都只能被原子解决一次。
5. Tool Policy 在 proposal 和 commit 前都必须成立。
6. Runtime、Channel 和 MCP Server 都不能自行授予 Tool authority。
7. App 和 Runtime 不直连受治理上游 MCP Server。
8. Runtime request 丢失后，旧 Interaction resolution 不得投给新 request。
9. 外部写结果不确定时不得自动重试。
10. 新接入 MCP Server 不要求在 App 或 Core 增加业务类型分支。

## 11. 实施顺序

### Phase 1：结构化用户输入

- 新增 `HumanInteraction(user_input)` 持久模型和事件；
- Core 接入 Runtime `InteractionRequested/resolve_interaction`；
- Conversation/Task 增加明确 waiting reason；
- 增加统一 Interaction API；
- App 实现受限 schema 表单。

### Phase 2：审批参数一致性

- Approval 保存并返回规范化 Tool 参数；
- UI 从保存参数生成安全关键展示；
- 复用现有稳定 ToolStep ID，并测试 Approval、ToolStep 和实际执行参数一致；
- 在审批返回后重新执行 Tool lookup、schema、call-specific policy 和 capability 校验；
- Tool 配置变化使未执行 Approval stale；
- 保留现有 ToolStep 单次提交和 `outcome_unknown` 语义。

### Phase 3：标准 MCP Elicitation 与 Client Channel

- MCP Client 接入 form Elicitation callback 和现代 InputRequired 多轮 driver；
- 将请求持久化为 `HumanInteraction(mcp_elicitation)`；
- App、CLI 和 Textual TUI 支持 accept、decline、cancel；
- CLI/TUI 支持 Agent、Task、Execution、Approval、Interaction 和 follow-up；
- URL Elicitation 默认拒绝，不自动导航。

### Phase 4：Jira 纵向闭环

- 经办人查询、选择、指派；
- transition 查询、字段选择、流转；
- 评论正文确认和写回；
- App、CLI/飞书可表达子集；
- stale、重复点击、重启和超时测试。

## 12. 验证场景

1. Agent 查询 Jira 可指派用户，App 选择精确 ID，批准后成功指派。
2. 用户完成选择但拒绝 Approval，Jira 没有写入。
3. Agent 在批准后改变参数，旧 Approval 不能用于执行。
4. App 和飞书同时提交同一 Interaction，只有一个成功。
5. App/Core 重启后 pending Interaction 可以从数据库恢复。
6. Runtime request 丢失后 Interaction 进入 `runtime_lost`。
7. transition 必填字段通过通用 schema 收集并进入最终 Approval。
8. transition 已失效时 Jira MCP 拒绝，系统不替用户选择其他流转。
9. Tool policy 在确认后被禁用，commit 被拒绝并标记 stale。
10. 外部写请求结果不确定时停止执行且不自动重试。
11. 非 Jira MCP Server 可以复用同一 user input 和 Approval UI。
12. App 和 Core 中不存在 Jira 业务类型判断。

## 13. 延后项及触发条件

| 延后能力 | 何时重新设计 |
|---|---|
| 多个并行 Interaction | Runtime 开始并行等待两个独立用户决定 |
| MCP Elicitation 跨重启恢复 | 有真实长时 Tool call 必须跨连接或进程恢复 |
| 独立 Grant/Snapshot | 现有 Approval + ToolStep 无法证明单次精确执行 |
| Tool/schema 版本摘要 | MCP Provider 支持调用期间热更新且出现真实竞态 |
| worker fencing | 同一 ToolStep 允许被多个 worker 恢复或接管 |
| 自动 reconciliation | `outcome_unknown` 大量出现且人工检查不可接受 |
| 无模型业务按钮 | 有明确产品需求提供不经过 Agent 的确定性操作 |
| Secret 表单 | 有受控 credential broker，且安全模型单独评审完成 |

## 14. Critic finding 的 YAGNI 处置

此前独立 Advisory Critic 的 finding 按当前 MVP 重新处置：

| Finding | MVP 处置 |
|---|---|
| 单次授权消费存在矛盾 | 接受核心问题；复用现有 Approval 单次解决和唯一 ToolStep，不新增 Grant |
| 展示可能与参数不一致 | 接受；展示和执行都读取 Approval 保存的规范化参数，不新增 Snapshot |
| policy/Tool 变化 | 接受最小闭环；commit 前重查，变化即 stale，不设计版本偏序 |
| Runtime 旁路或重复审批 | 接受为现有架构不变量；Gateway 是唯一外部 Tool 路径 |
| MCP Elicitation 恢复 | form Elicitation 已支持；仅跨连接/重启恢复延后 |
| 不可信 schema/Secret | 部分接受；限制 schema、标记来源、禁止 Secret |
| fencing/reconciliation | 延后；当前复用单执行器，结果不明只停止且不重试 |
| 无模型业务按钮会泄漏 Jira | 接受范围收缩；MVP 只通过 Conversation/Agent 编排 |

这份处置同时守住两条线：不为未来假设增加抽象，也不省略当前路径实际依赖的原子性、参数一致性、权限和失败语义。

## 15. 最终分层

```text
标准 MCP Tools          发现并执行外部能力
Knoa HumanInteraction   收集当前 Agent 所缺的结构化用户输入
Knoa Approval           确认参数完整的副作用 Tool
Knoa ToolStep           单次 commit、结果和 outcome_unknown
Knoa App                通用展示，不理解 Jira 等业务类型
```

因此，用户在 Knoa 中选择“转给谁”是通用 HumanInteraction；最终“把工单转给该用户”是经过 Knoa Approval 的标准 MCP `tools/call`。Knoa 保持通用，同时 MVP 只实现当前 Jira 闭环真正需要的能力。
