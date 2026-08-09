# Knoa 个人 Agent 产品与能力路线图

> 状态：已确认的产品方向与实施路线图
>
> 日期：2026-08-09
>
> 产品名称：小诺 · Knoa
>
> 目标：用户通过手机随时联系小诺，由小诺调用电脑、本地数据、Skill、MCP
> 和外部服务，持续完成真实工作并交付结果。

## 1. 执行决策

Knoa 不应被定义为“远程控制电脑的软件”，也不应只围绕聊天界面建设。
它的目标是一个始终在线、可扩展、可恢复、能主动工作的个人执行代理。

产品形态采用一个 Core、多个入口：

```text
                         Knoa Core
              能力、任务、记忆、权限、成果
                   标准命令 / 标准事件
          ┌──────────────┼──────────────┐
       飞书 Channel      Knoa App       Knoa CLI/TUI
       快速联系与通知     移动工作台       本地交互与运维
```

长期应建设 Knoa 移动 App，但它不是桌面服务状态面板，也不是飞书聊天窗口的简单
复制。App 的职责是承载飞书无法优雅表达的复杂任务、权限、文件、成果和能力管理。

实施顺序必须是：

```text
能力扩展平台（Skill / MCP）
                    ↓
持久任务与主动执行平台
                    ↓
增强飞书远程入口
                    ↓
Knoa 移动工作台
```

App 不能先于稳定的任务和能力协议成为新的业务逻辑中心。

## 2. 产品北极星

用户可以在手机上告诉小诺：

- “整理今天收到的资料，提炼风险并把报告发给我。”
- “检查项目状态，处理能自动处理的问题，需要决定时再问我。”
- “把这份文件同步到知识库，创建工单并通知相关人员。”
- “每天早上汇总日历、邮件、任务和重要消息。”
- “在电脑上完成这项工作，过程中保留证据，结束后交付成果。”

小诺需要具备以下闭环：

```text
理解目标 → 制定步骤 → 选择能力 → 执行 → 等待确认 → 恢复执行
        → 验证结果 → 保存成果 → 主动通知 → 形成长期记忆
```

衡量产品价值的核心不是鼠标移动次数或工具调用数量，而是用户交付给小诺的真实
工作有多少能够安全、正确、持续地完成。

## 3. 当前能力审计

### 3.1 已具备的 Core 基础

当前前向运行时已经具备：

- 严格、版本化、带 principal/session 所有权的 Core API；
- 标准流式事件：思考、正文、计划、工具调用、工具结果、成果、警告和终态；
- ReAct 循环、模型适配、多模型配置和失败切换；
- 主机文件读写、Shell、屏幕、窗口、键盘、鼠标、剪贴板和桌面通知；
- Web 搜索、网页抓取、天气和汇率；
- 用户记忆、会话记录、上下文压缩和 token/调用观测；
- Artifact 的会话所有权、上传、下载和飞书文件/图片交付；
- capability、effect、risk、确认、取消和唯一 ToolStep 提交边界；
- Core 与 Channel 解耦，飞书只依赖 CoreClient；
- CLI/TUI、飞书 Channel 和后台服务生命周期。

当前生产注册的工具是基础工具集合，覆盖电脑操作和少量网络查询，已经能够支撑
简单的端到端电脑任务。

### 3.2 尚未具备或尚未接入

以下能力是实现产品北极星的主要缺口：

- 当前已加载数据型 Skill，并支持按请求、工具和权限动态匹配；
- 当前已具备 MCP discovery、lifecycle、官方 HTTP/stdio Client 和 ToolStep Adapter；
- 邮箱、日历、Jira、语雀、GitHub、网盘等业务能力仍需逐步以 MCP Server 接入；
- 各 MCP Server 的账号、Secret 和授权生命周期仍需按真实服务逐个完成；
- 没有持久任务队列、定时任务、Webhook 或主动触发器；
- 运行依附于 WebSocket 连接，连接断开会取消正在执行的任务；
- 没有任务事件持久化、断线重放、暂停继续和进程重启恢复；
- 没有多任务工作台、任务依赖、优先级和成果空间；
- 飞书当前只接收文本和图片，不接收普通文件、音频、视频等输入；
- 缺少浏览器、文档和业务系统的语义级操作，仍会退化为机械 GUI 操作。

## 4. 渠道与客户端定位

### 4.1 飞书 Channel

飞书是长期保留的快速入口，而不是临时替代品。

适合：

- 随时发送自然语言任务；
- 可靠的手机推送；
- 简单的确认与取消；
- 接收摘要、结果、图片和文件；
- 服务启动、停止、异常和任务完成通知。

固有限制：

- 卡片长度、Markdown、表格数量和更新频率受平台限制；
- 长输出需要拆分，复杂过程容易失去整体结构；
- 难以承载任务列表、成果库、权限中心和复杂设置；
- 难以完整利用手机相机、文件、语音和系统分享入口；
- 产品能力与体验受第三方平台规则约束。

### 4.2 Knoa 移动 App

移动 App 是复杂工作的个人工作台，重点提供：

- 对话、语音和结构化任务输入；
- 多任务列表、任务详情、阶段和实时进度；
- 权限请求、生物识别确认和风险说明；
- 文件、照片、相机、录音和系统分享菜单；
- 成果预览、下载、收藏、再次执行和分享；
- Skill、MCP 的状态和授权管理；
- Push 通知、离线消息和断线重连；
- 记忆、偏好、设备和安全策略管理。

App 只消费 Core/Gateway 的标准协议，不直接调用工具、不访问 Core 内部对象、不复制
Agent 决策逻辑。

### 4.3 Knoa CLI/TUI

CLI/TUI 长期保留，用于：

- 电脑本地快速交互；
- 服务启动、停止、重启、状态和诊断；
- 自动化脚本和一次性 `--ask`；
- 高级配置、调试和开发者操作。

交互式 `pca` 首页恢复简洁的静态 `Knoa` ASCII 品牌字和“小诺”副标题。后台服务、
`--status`、`--restart`、`--ask`、JSON 输出和日志不显示 Logo，也不新增 figlet 依赖。

## 5. 目标架构

```text
┌──────────────────────────────── Clients / Channels ────────────────────────────────┐
│ Feishu Channel        Knoa Mobile App        CLI/TUI        Future Channels       │
└───────────────────────────────┬────────────────────────────────────────────────────┘
                                │ authenticated commands / resumable event stream
┌───────────────────────────────▼────────────────────────────────────────────────────┐
│ Secure Gateway / Channel Runtime                                                   │
│ device identity · pairing · TLS · push routing · rate limits · protocol adaptation │
└───────────────────────────────┬────────────────────────────────────────────────────┘
                                │ principal-scoped Core API
┌───────────────────────────────▼────────────────────────────────────────────────────┐
│ Knoa Core                                                                          │
│                                                                                     │
│ Task Service    Agent Runtime    Approval Service    Memory    Artifact Service     │
│ Event Journal   Scheduler        Trigger Service     Audit     Observability        │
│                                                                                     │
│ Capability Registry → Built-in Tools / Skills / MCP Servers                        │
└───────────────────────────────┬────────────────────────────────────────────────────┘
                                │ verified ToolStep commit boundary
┌───────────────────────────────▼────────────────────────────────────────────────────┐
│ Computer · Files · Shell · Browser · Email · Calendar · SaaS · Knowledge · Network │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 5.1 不变量

1. Core 不导入飞书、App 或其他具体 Channel。
2. 所有入口只能通过经过认证的 principal 调用公开协议。
3. Built-in 与 MCP 工具必须经过同一个 ToolStep；Skill 只能编排这些工具。
4. 工具发现和 Schema 元数据不能自行授予权限。
5. 任务生命周期属于 Core，不能属于某一条 WebSocket 连接。
6. 客户端断开不等于用户取消任务。
7. 权限确认是持久任务状态，而不是连接内的临时 Future。
8. 所有成果通过 Artifact 引用交付，不泄露 Core 本机路径。
9. 飞书与 App 对同一标准事件采用各自最合适的表现形式。
10. 不为旧实现保留双运行时、兼容分支或语义适配层。

## 6. Phase A：能力扩展平台

> 进度（2026-08-09）：Phase A 已启动。A1 ExtensionManager 生命周期基础、A2
> MCP Streamable HTTP 与本地 stdio 纵向闭环、A3 Skill Package 基础、A4
> 本地 MCP 包发现和 A5 工具来源、权限、风险与确认策略的 typed 管理描述均已完成。
> A6 已支持 Agent 发起、本地目录暂存校验、用户确认、原子安装和运行时动态激活。
> Phase A 后续工作是按需导入真实 MCP 包并完成私有账号部署验证。
> 第三方本地 MCP 的 OS 级 CPU、内存、文件系统和网络沙箱列为最低优先级
> Backlog；当前独立进程、超时、最小环境和故障隔离已经满足主线推进要求，
> 不阻塞 Phase B。

### 6.1 Skill Package

定义稳定、可验证的 Skill 包结构，至少包含：

- manifest、名称、版本和描述；
- 使用说明、触发条件和上下文资源；
- 所需 Built-in/MCP 工具和权限；
- 安装、启用、禁用、升级和卸载生命周期；
- 健康检查和诊断信息；
- 明确的来源、签名或信任级别。

Skill 是能力编排和领域知识，不得绕过 ToolStep 直接执行代码或获得额外权限。

A3 已落地数据型 Skill 包、严格 manifest、包根目录约束、文本资源大小限制、
工具/权限依赖检查和按请求触发的选择性上下文注入。安装、签名、Secret 引用与
管理面生命周期留到具备相应信任边界后实现，不提前扩张 manifest。

### 6.2 MCP Runtime

MCP 接入必须包含：

- stdio、HTTP/SSE 等明确支持的 transport；
- Server 配置、启动、停止、重连、超时和健康检查；
- 工具命名空间和冲突处理；
- discovery Schema 校验与大小限制；
- MCP Tool 到 ToolBase Policy 的显式映射；
- capability、effect、risk 和 confirmation 配置；
- 调用取消、输出限制、Secret 隔离和审计；
- 故障 Server 隔离，不能阻止 Core 启动。

未知或未配置的 MCP 权限默认禁用，不能从 Server 自述中自动推导高权限。

### 6.3 外部服务与凭据边界

邮箱、日历、知识库、工单和代码平台统一由各自 MCP Server 负责业务协议、OAuth/API
Token、账号健康和重新授权。Core 只接收标准 MCP 工具，不理解服务类型，也不保存
服务专用字段。

本地 stdio MCP 只继承 manifest 明确列出的环境变量名；值不进入 manifest、模型上下文
或工具 Schema。读取、写入、数据外发与删除操作仍由 Core 的本地 effect/capability/risk
策略分级，高风险外部副作用继续经过统一确认。MCP Server 自己负责提供方级审计，Core
负责统一工具调用审计。

### 6.4 Phase A 验收

- 新增 Skill 不修改 ReActLoop 或 Channel；
- 新增 MCP Server 不修改 TaskService、AgentRuntime 或 CoreServer；
- 所有动态工具进入现有 capability/confirmation/ToolStep 边界；
- 一个故障扩展不会影响其他扩展或基础工具；
- `/tools` 和未来 App 能显示来源、权限、状态和健康信息；
- 至少打通一个知识类、一个工作流类和一个文件类真实集成。

### 6.5 最低优先级 Backlog

- 在出现明确的不可信第三方包或资源争用场景后，再引入通用 MCP ProcessLauncher
  的 OS 级资源沙箱策略；
- 沙箱实现必须位于通用进程启动边界之后，不得向 Core 引入 systemd、容器产品、
  第三方服务或具体 MCP 包的业务概念；
- 该项不阻塞能力接入、持久任务、飞书增强或移动工作台。

## 7. Phase B：持久任务与主动执行

> 进度（2026-08-09）：Phase B 正向设计已完成，详见
> `docs/knoa-durable-task-design.md`。B1-B3 已完成：持久 Task 聚合、EventJournal、
> 连接无关 TaskExecutor、`after_seq` 重放、持久审批以及飞书/TUI 标准确认命令均已
> 进入生产路径；公开 Run 协议和连接所有的确认逻辑已经删除。B4 已完成：重启时
> 无法证明执行结果的 `running` Task 保守进入 `paused`，只有显式 `resume_task`
> 才会重新排队，避免外部副作用被自动重试；执行器已支持不同 session 有界并发，
> 同一 session 仍在持久 claim 边界严格串行；Task 详情与有界游标列表已经进入
> Core API，且不会暴露内部 lease、worker 或 revision 字段。
> 持久创建事务同时限制全局 128 个、单 principal 32 个非终态 Task；幂等重试先于
> 容量判断，满载时通过标准 `resource_exhausted` 返回。
> 每次 claim 现在都会创建持久 Attempt；ToolStep 在真实提交前写入 `committing`，
> 提交后保存 typed result。重启遇到未完成 commit 会转为 `outcome_unknown` 并暂停；
> 恢复时必须显式设置 `acknowledge_outcome_unknown`，相同 ToolStep 仍禁止自动重放。
> 当前进程 checkpoint 失败也会在一个事务内隔离 ToolStep、中断 Attempt 并暂停 Task；
> `pause_task` 对运行中任务采用持久 `pause_requested` 和安全边界暂停，重启不会丢失。

### 7.1 Task 模型

将连接内 `Run` 提升为 Core 所有的持久 `Task`：

```text
queued → running → waiting_approval → running → completed
                  ↘ paused / failed / cancelled
```

Task 至少记录：

- task_id、principal、session、目标和创建来源；
- 当前状态、阶段、尝试次数和时间信息；
- 关联 run、工具调用和确认请求；
- 成果 Artifact、最终摘要和失败原因；
- 事件序列号和可恢复检查点；
- 调度、触发器、优先级和父子任务关系。

### 7.2 Event Journal

- 所有公共任务事件持久化并按 task_id 单调排序；
- 客户端用 `after_seq` 重连和重放；
- 飞书、App 和 CLI 不依赖内存中的瞬时 stream；
- 事件日志有容量、保留和压缩策略；
- reasoning 是否对外展示由 Channel 策略决定，不改变 Core 权限。

### 7.3 连接独立执行

- 客户端断开后任务继续；
- 用户显式取消才改变任务取消状态；
- Core 重启后恢复可恢复任务，明确终止不可恢复的步骤；
- 等待确认可以跨连接、跨客户端和跨进程重启；
- 同一确认只能原子解决一次；
- 副作用步骤使用幂等键，恢复时不得重复提交。

### 7.4 Scheduler 与 Trigger

支持：

- 一次性时间、周期和 Cron；
- 邮件、Webhook、文件变化和业务事件触发；
- 触发器创建的任务使用明确 principal 和 capability profile；
- 高风险操作仍进入持久确认状态；
- 失败重试有上限、退避和通知。

> 进度（2026-08-09）：B5 已启动。一次性、固定周期和标准五段 Cron 的统一时间
> 语义已实现，Cron 使用显式 IANA 时区，周期任务以初始时间为锚点避免累计漂移。
> Schedule 与每次 Occurrence claim 已持久化；生产 dispatcher 使用稳定 occurrence ID
> 幂等调用 TaskService，并提供有界指数退避、过期 lease 恢复和 Core API 创建/详情/列表。
> 计划暂停/恢复也已接入：周期计划恢复时跳过停机期间的积压，过期的一次性计划不会
> 静默补跑。认证 Trigger ingress 也已进入 Core：外部 event ID 持久去重、独立
> dispatcher 有界重试、暂停时拒绝新事件并冻结未 claim 事件，payload 以不可信数据
> 进入 Task。Core 已增加按 principal 排序、可重放的持久 Task event feed；飞书
> Channel 使用持久 cursor 独立订阅该标准流，前台卡片任务去重，后台 Schedule/Trigger
> Task 完成、失败或取消后主动交付结果。下一步是独立 HTTP webhook adapter。

### 7.5 Phase B 验收

- 手机断网或 App 进入后台，任务继续运行；
- 重连后能从最后事件序号恢复完整状态；
- 服务重启不会丢失 queued/waiting_approval 任务；
- 一个用户可查看、取消和管理多个任务；
- 定时任务能执行真实 Skill/MCP 工作并主动交付成果；
- 不产生重复外部副作用。

## 8. Phase C：增强飞书远程入口

在持久任务协议上增强飞书：

- 接收普通文件并注册为 Core Artifact；
- 接收音频并通过可配置能力转写；
- 支持 `/tasks`、`/task <id>`、`/stop <id>`；
- 新消息可以创建新任务，而不是只能等待当前任务结束；
- 卡片只显示适合飞书的当前摘要，完整时间线由任务协议保存；
- 等待确认、失败、恢复和完成均主动通知；
- 长结果优先生成报告 Artifact，卡片展示摘要而非无限拆分；
- 可生成深链，未来跳转到 App 的任务详情。

### Phase C 验收

- 飞书可独立完成文本、图片、文件和语音任务入口；
- 长任务不依赖一张持续更新的卡片存活；
- 用户能从飞书查询多个任务状态；
- 超长内容以摘要加成果文件优雅交付；
- 飞书故障不会取消 Core 中的任务。

## 9. Phase D：Knoa 移动工作台

### 9.1 第一版范围

- 登录、设备配对和安全连接；
- 对话、语音和系统分享入口；
- 任务列表、状态筛选和任务详情；
- 实时事件流和断线恢复；
- 确认、拒绝、取消和重新执行；
- 图片、文件、相机和录音上传；
- Markdown、代码、表格、图片和文件预览；
- Push 通知和通知跳转；
- Skill/MCP 状态查看。

### 9.2 后续范围

- Skill/MCP 安装和权限管理；
- MCP Server 授权；
- 定时任务和触发器编辑；
- 成果库、搜索、收藏和分享；
- 记忆和偏好管理；
- 任务模板和可复用工作流。

### 9.3 技术方向

移动端同时支持 Android/iOS 时优先评估 Flutter。客户端必须从 Core Schema 生成或严格
复用协议模型，避免手写漂移。

当前 Core 只允许 loopback 明文 WebSocket，这是正确的安全默认值。移动 App 不得直接
把该端口暴露到公网。应增加独立 Secure Gateway，至少包含：

- TLS；
- 设备配对和可撤销设备身份；
- 短期访问凭据；
- principal 绑定；
- 请求与上传大小限制；
- 审计、限流和异常设备撤销。

个人自用的早期版本可以用 Tailscale/WireGuard 验证产品，但这不是最终公共部署协议。

### Phase D 验收

- App 后台或断网不影响任务执行；
- Push 能可靠通知确认和任务终态；
- App、飞书和 CLI 对同一任务看到一致状态；
- App 没有任何绕过 Core 权限边界的执行路径；
- 丢失设备可以立即撤销且不能继续访问历史、任务和 Artifact。

## 10. 明确不做

- 不先做仅显示服务状态的桌面 App；
- 不复制一套只比飞书漂亮的聊天 UI；
- 不把飞书、Flutter、Push 或移动端逻辑写入 Core；
- 不允许 Skill 或 MCP 绕过统一工具提交边界；
- 不把客户端连接当作任务所有者；
- 不直接向公网暴露当前 loopback WebSocket；
- 不为已删除的旧 Agent、Scheduler 或 MCP 实现保留兼容路径；
- 不在缺少真实使用场景时建设复杂的低代码工作流编辑器。

## 11. 工程原则

- **高内聚**：任务、能力、MCP Server、确认、Artifact 各有唯一所有者。
- **低耦合**：Channel/App 依赖公开协议，不依赖 Core 内部类型和实例。
- **YAGNI**：先打通真实工作闭环，再扩展通用平台能力。
- **安全默认拒绝**：未知扩展、权限、Secret 和外部副作用默认禁用。
- **正向设计**：按目标模型实现，不保留旧运行时或双语义兼容。
- **可恢复优先**：长期工作不能依赖进程内对象或活跃连接。
- **交付优先**：任务最终必须产生可验证结论或成果，而不只是工具调用记录。
- **语义工具优先**：业务 MCP 工具优先于脆弱的鼠标键盘模拟。
- **Channel 自治**：标准事件保持中立，各 Channel 自行选择表达方式。

## 12. 质量指标

每阶段至少持续观测：

- 真实任务完成率和人工接管率；
- 平均任务耗时、失败阶段和恢复成功率；
- 工具/MCP 成功率和延迟；
- 重复副作用为零；
- 确认请求正确到达率和原子解决率；
- 断线事件恢复完整率；
- Artifact 交付成功率；
- 飞书卡片降级和拆分率；
- token、模型调用和工具调用成本；
- Skill/MCP 健康状态和故障隔离效果。

## 13. 设计文档状态

已按依赖顺序形成两份独立设计：

1. **Knoa Capability Extension Design**：Skill、Built-in/MCP Tool 和统一
   ToolStep 接入契约。
2. **Knoa Durable Task Design**：Task 聚合、Event Journal、确认持久化、重放、
   调度与恢复语义。

移动 App 的信息架构和视觉设计应在这两份协议稳定后开始，避免用 UI 倒逼 Core
产生临时接口。
