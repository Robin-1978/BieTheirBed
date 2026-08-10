# 小诺 Conversation 重构实施计划

本计划直接面向目标架构，不兼容 Chat-as-Task 的旧公共语义。

## Phase 1：Session Context（已完成）

- 扩展 Session 存储为有序 Turn、rolling summary 和覆盖位置；
- 实现 `SessionContextService` 的 load、commit 和按预算 compact；
- 保留完整历史，模型上下文只使用摘要与最近 Turn；
- 为 App、飞书多轮和压缩恢复增加测试。

完成标准：同一 Session 的下一轮使用上一轮历史；长 Session 生成持久化摘要；重启后
摘要仍生效；新 Session 不继承旧聊天历史。

## Phase 2：ChatTurn（已完成）

- 新增 Conversation Repository、Service 和 Turn stream；
- ConversationService 组装 Session 上下文并调用 AgentRuntime，不经过
  TaskRepository/TaskExecutor；
- 将 Session load/save 从 AgentRuntime 移出，AgentRuntime 只接受 RunRequest、
  RunContext 并返回 RunSignal/RunOutcome；
- reasoning/content 只作为临时流信号，完成时保存合并结果；
- 工具、Approval、Artifact 作为 Turn 子资源持久化；
- 断线恢复读取 Turn 快照，不重放 token。

完成标准：普通聊天数据库中不产生 runtime_task；一次长回答不会增加 principal Task
feed；审批后继续同一 Turn。

## Phase 3：Core Protocol（已完成）

- 增加 Session Turn 创建、查询、订阅、取消和审批命令；
- Task 协议删除 `origin=chat` 默认语义；
- Task reliable feed 只保留粗粒度生命周期事件；
- 长连接订阅与普通请求故障隔离。

完成标准：落后订阅不因历史 token 导致 256 队列溢出；Task Feed 中不存在聊天正文
和思考 delta。

## Phase 4：Gateway 与 App（已完成）

- App 聊天页改用 Conversation API 和 Turn stream；
- 新话题只创建 Conversation Session；
- App 初始化读取 Session/Turn 快照，不从 0 重放 principal feed；
- Task 页只查询 Task；
- Push 只处理后台 Task 和待关注状态。

完成标准：App 问答即时出现运行状态；重连后恢复当前完整正文；App 聊天不触发飞书
卡片；任务筛选和控制保持可用。

## Phase 5：飞书 Channel（已完成）

- 飞书 open_id 绑定独立 Conversation Session；
- 普通消息创建 ChatTurn 并更新同一张卡片；
- Approval 绑定当前 Turn/Card；
- `/new` 创建新 Session；
- 后台 Task watcher 只处理 Task，不处理 ChatTurn。

完成标准：飞书多轮历史与持久化摘要生效；App/飞书上下文和输出不串线；后台 Task
仍能主动通知。

## Phase 6：清理与发布（实现与测试完成，待构建部署）

- 删除 Chat origin、Chat Task 列表分支和逐 token Task feed 写入；
- 更新 OpenAPI、TypeScript 模型、产品与部署文档；
- 全量 Python、移动端类型与单测；
- 构建 ARM64 APK、发布、提交、推送、重启并验证公网和飞书。

当前验证基线：Python 全量 `619 passed`；App OpenAPI contract、TypeScript、Vitest
全部通过。Task 流式输出已迁入按 iteration 合并的 `ExecutionTrace`，旧 token 事件在
数据库启动迁移中先归并 Trace，再从 Task journal 与 principal feed 清理。

## 迁移原则

- 旧 Chat Task 不保留公共兼容语义；其旧 token 事件只迁入内部 ExecutionTrace；
- 不为旧 Chat Task API 增加兼容适配器；
- 现有 Session transcript 原文继续作为 Session 历史，不伪造新的 ChatTurn；
- Task 后台数据和控制语义保持；
- 每个 Phase 必须有针对性测试后才进入下一层调用方。
