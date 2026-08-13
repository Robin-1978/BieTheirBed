# MCP Event Task 与人机协作设计

## 目标

Knoa Platform 以标准 MCP Resource/Notification 接入外部能力。Jira 只是首个完整示例，平台不识别 Jira URI、字段或业务状态。

外部事件必须进入唯一的 Product Task 模型，并在 APP、飞书及其他通道中保持一致：

```text
MCP Resource inventory / notification
  -> trusted resource route
  -> Product Task Definition
  -> Task Execution
  -> Agent Turn
  -> Task timeline / result
```

## 聚合与身份

- 一个不可变 MCP 事件 Resource 对应一个 Product Task Definition。
- Resource URI 与 MCP server ID 共同生成稳定幂等键。
- 首次发现事件后立即创建 `launch_reason=event` 的 Execution。
- Product Task 保留 MCP 来源元数据，但平台不解释业务语义。
- 每个外部事件派生独立 Agent Session，避免不同工单共享 Codex Thread。
- 同一 Product Task 的后续执行复用该 Session，让 Agent 保留其自身上下文。

## 人类参与

Task Execution 完成后，用户可以在通用执行详情中补充：

- 文字说明；
- 日志或普通文件；
- 图片等多模态证据。

补充内容创建同一 Product Task 下的新 Execution，标记为 `follow_up`。它不是修改历史结果，也不是 Jira 评论特例。新执行复用 Task 的 Agent、Session、工具策略与优先级。

运行中的 Task 不接受第二个持久 Execution，以保持串行上下文和明确的审计顺序。用户可等待当前执行结束，或先停止再补充。Agent SPI 的 `steer_turn` 仍可供低延迟通道使用，但不是 Task 持久协作的主路径。

## 附件边界

APP 将补充附件上传到 Product Task 的专属 Session。Platform 仅存储 Artifact 引用并在本次 Agent Turn 中授权读取，不把文件内容塞入业务协议。删除 Task/Execution 时继续沿用现有 Artifact 引用清理策略。

## Jira 示例

Jira MCP Server 负责：

- 使用 Token 查询分配给当前用户的问题；
- 物化 issue、评论、附件与 manifest；
- 通过标准 Resources 与 Resource notifications 暴露不可变分配事件；
- 通过标准 MCP Prompt 暴露人工分析模板。

Knoa Platform 负责标准 MCP Host、事件路由、Task 生命周期、Agent 执行、人机协作和通道展示。它不包含 Jira JQL、附件下载或问题分析业务逻辑。

事件 Resource 只包含固定分析目标、经过严格校验的 issue 标识符和受控本地位置。Jira description、评论、日志与附件始终作为不可信证据保存在独立目录或数据 Resource 中，不能拼接成 Platform 信任的 Task 指令。MCP Prompt 是可发现、可人工选用的模板，不是自动触发协议。

Jira 示例使用有保留期的不可变 assignment event Resource。平台保证对当前 inventory 中仍存在的事件最终收敛；它不把易变化的 issue 渲染内容 hash 当作事件身份，也不会因为 Knoa 自己写回评论而创建新事件。是否处理启用前的存量由 Server 建立事件基线的策略决定。

## 失败与幂等

- Resource 重复出现不会重复创建 Task。
- Session 暂不可用时保留事件，后续 reconcile 重试。
- Host 完整遍历标准 `resources/list` 分页，并仅在 Server 协商了 `resources.subscribe` 时逐个订阅 inventory 返回的具体 URI；集合 URI 不隐含对子 URI 的通配订阅。
- URI 授权是 Knoa 本地策略：scheme、authority 和规范化路径段必须匹配，拒绝 userinfo、fragment、路径穿越与歧义编码。
- MCP 包更新采用托管目录原子替换，失败恢复上一运行版本。
- 人类补充请求使用客户端幂等 ID，网络重试不会产生重复 Execution。
- 一个 Product Task 同时只允许一个非终态 Execution。
- 服务中断后的旧 Codex Turn 若结果不确定，不自动重放；用户在同一 Product Task 下创建 `rerun` Execution，保留旧执行审计记录。

## 设计挑战与结论

1. **直接让 APP 展示 `runtime_tasks`**：会形成两套产品模型，拒绝。
2. **所有 Jira 工单共用路由 Session**：会造成 Codex Thread 上下文串扰，改为每事件独立 Session。
3. **完成后直接 `steer_turn`**：运行时已无 active Turn，且缺少持久审计，改为新的 `follow_up` Execution。
4. **为 Jira 增加专用 APP 页面**：不利于后续 MCP 接入，拒绝；使用统一 Task Execution UI。
5. **把 MCP Prompt 当成自动触发器**：不符合 MCP 语义；Prompt 是可发现模板，Resource notification 才负责事件唤醒。
6. **把 MCP 用户数据提升为受信指令**：存在 prompt injection 风险，拒绝；固定目标与不可信证据分层。
7. **假设父 Resource 订阅覆盖所有子 URI**：不是 MCP 标准语义，拒绝；按协商能力对 inventory 中的具体 URI 建立订阅，并以周期完整 inventory 恢复通知丢失。
8. **把 `thread/resume` 当作断线重放**：动态工具调用可能已有未知结果，拒绝；旧 Execution 保持失败/未知，新建审计明确的 rerun。

## Codex App Server 后续边界

Codex Provider 是 Agent 实现，不替代 Standard MCP Host。Platform 只通过能力网关向 Agent 暴露授权后的 Tools 与 Artifacts；Resources、Prompts、Notifications 仍由 Platform 的标准 MCP Host 处理。

- Codex App Server 使用隔离、生成式配置，禁止继承未经 Platform 授权的外部 MCP/apps/plugins，启动后发现旁路入口即 fail closed。
- Knoa 是外部工具审批的唯一权威。无法关闭或可靠映射 Codex 侧重复审批时，不启用该直连路径。
- App Server 上游认证不代表 Knoa principal 身份；Thread/Session/workspace 的归属始终由 Platform 验证。
- 第一阶段只允许单次最小授权，拒绝 session grant、exec-policy amendment 等扩大后续权限的决策。
- `threadId/turnId/itemId/callId` 与工具幂等键必须持久绑定；断线时未确认的写操作进入结果不确定状态，不自动重放。

该设计维持高内聚、低耦合：MCP Server 高内聚业务，Platform 高内聚通用执行与协作，不新增业务插件框架或工作流 DSL。
