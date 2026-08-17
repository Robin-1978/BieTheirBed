# Knoa Mobile UI 与交互设计

> 状态：与 Mobile App 权威架构对齐的目标 UI
> 更新日期：2026-08-17
> 原则：Account -> Workspace -> Resources/Work/Nodes；Workspace 可脱离 Node 浏览和配置；Conversation 创建时绑定 Node，Task 启用前部署到 Node；任何页面都有明确上行路径。

架构、状态所有权和实施顺序见
[knoa-mobile-app-design.md](./knoa-mobile-app-design.md)。本文只定义用户看到的导航、页面和行为。

## 1. 产品导航模型

App 不是五个并列功能的集合，而是三个逐层进入的工作范围：

```text
Account
  -> Workspace
    -> Resources: Agent / LLM / Skill / MCP
    -> Work: Conversation / Task
         -> required binding/deployment Node for content and execution
    -> Nodes: configuration / deployment / status
```

每一层拥有自己的页面和设置：

| 层级 | 核心页面 | 设置作用域 |
| --- | --- | --- |
| Account | Workspace 列表、最近使用 | 帐号安全、Hub、App 外观/语言/更新 |
| Workspace | 概览、Conversation、Task、Agent、LLM、Skill/MCP、Node、成员 | Work、共享资源、Node Desired State、成员、授权、审计 |
| Execution context | 当前 Invocation/Attempt、能力状态、诊断 | placement Node 的连接和运行状态 |

“随时管理”通过顶部 breadcrumb、Account 头像和明确的返回 Workspace 实现，不把不同层级做成并列
Tab。App 不使用持久底部导航；对话输入框必须独占底部区域。

## 2. Account 首页

登录成功后最稳定的根页面是 Account 首页：

```text
┌────────────────────────────────────┐
│ Knoa                         Robin ●│
├────────────────────────────────────┤
│ Workspace                           │
│                                    │
│ ┌────────────────────────────────┐ │
│ │ Personal Workspace       Owner │ │
│ │ 1 个 Node 在线 · 最近使用       │ │
│ └────────────────────────────────┘ │
│                                    │
│ ┌────────────────────────────────┐ │
│ │ Work Workspace           Member│ │
│ │ 2 个 Node · 1 个在线            │ │
│ └────────────────────────────────┘ │
│                                    │
│ + 创建或加入 Workspace              │
├────────────────────────────────────┤
│ 帐号安全 · Hub · App 设置 · 更新     │
└────────────────────────────────────┘
```

- Account 首页不需要 Node。
- Account 头像/菜单从 Workspace 和 Node 页面也始终可达。
- 退出帐号、切换 issuer 和密码恢复都只在 Account scope 出现。

## 3. Workspace Shell

选择 Workspace 后进入 Workspace scope：

```text
┌────────────────────────────────────┐
│ ‹ Robin / Personal Workspace   ●   │
├────────────────────────────────────┤
│ 3 个 Node · 2 个成员 · 4 个资源      │
│                                    │
│ Robin Desktop            在线      │
│ 当前 App 已绑定             [进入]  │
│                                    │
│ Office PC               离线       │
│ 当前 App 已绑定             [详情]  │
└────────────────────────────────────┘
```

Workspace 首页使用 overview + drill-down，不使用底部或常驻局部 Tab：

- Agent/LLM/Skill/MCP 入口：共享资源 Published Spec、grant 和使用情况；
- Node 卡片/入口：目录、presence、绑定、配对、Desired State、Deployment、rollout 和进入执行上下文；
- 成员入口：成员、角色和邀请；
- 设置入口：Workspace 属性、授权和审计。

顶部 `‹ Robin / Personal Workspace` 可返回 Account 或切换 Workspace。没有 Node 时仍可完整使用这些
页面。

## 4. Work Execution Shell

打开 Conversation/Task 后进入 Workspace Work 页面；读取 Node 内容或继续执行时连接其绑定/部署 Node：

```text
┌────────────────────────────────────┐
│ ‹ Personal Workspace               │
│ Robin Desktop · 在线 · Relay    ●   │
│ [ 对话 ]  [ 任务 ]              ⋯  │
├────────────────────────────────────┤
│                                    │
│             当前 Node 页面          │
│                                    │
└────────────────────────────────────┘
```

Work 页面只把两个最高频对象放在顶部紧凑切换器：

- 对话：Conversation、附件、语音、Approval、Interaction；
- 任务：Task、Execution、Automation、结果和确认；

顶部 `⋯` 执行上下文菜单进入低频页面：

- 查看 Workspace Node 详情：Deployment、能力状态、配置同步和诊断；
- Node 状态：连接、版本、Relay/direct 和当前执行会话；
- 断开当前 Node；在另一 Node 新建 Conversation，或修改 Task 的未来 Deployment。

共享 Agent、LLM、Skill、MCP 和 Node Desired State 的编辑入口属于 Workspace。Node 菜单只能
deep-link 到所属 Workspace 的 Node detail，不能再提供一份并行的共享资源编辑器。

顶部第一行始终提供返回 Workspace；Account 头像保持可达。V1 Conversation binding 在会话内不变；
Task 改变 Deployment 只影响未来 Execution，不更换 Task ID 或父级。

## 4.1 为什么没有底部导航

Chat 是 Knoa 的高频主界面，底部同时承担：

- 多行输入框；
- 添加附件、拍照和文件；
- 语音录制/停止；
- 发送按钮；
- pending attachment 与上传进度；
- 键盘和系统 safe area。

在 composer 下方再放导航会减少对话可见高度、造成键盘动画抖动，并增加发送附近误触。因此底部
永远只属于 composer；页面导航放在顶部，管理入口放入层级页面和 Node 菜单。

## 5. 默认进入位置

层级固定，但用户可以选择启动偏好：

- Account 首页；
- 上次 Workspace；
- 上次 Node 的对话；
- 上次 Node 的任务。

推荐默认恢复上次上下文：

```text
Robin
  -> Personal Workspace
    -> 对话
      -> placement: Robin Desktop
```

即使视觉上直接显示对话，导航栈仍必须包含 Workspace 和 Account。用户点击返回时按以下顺序上行：

```text
Conversation detail -> Workspace Work -> Workspace -> Account -> exit App
```

恢复失败按最接近的有效父级降级：

```text
Node 失败 -> Workspace / Nodes
Workspace 无权限 -> Account / Workspaces
Account session 失效 -> Login
```

## 6. 进入、切换和退出 Node

Workspace Node 列表：

```text
┌────────────────────────────────────┐
│ Personal Workspace / 节点           │
├────────────────────────────────────┤
│ Robin Desktop       在线 · 已绑定   │
│                     [进入 Node]     │
│ Office PC           离线 · 已绑定   │
│                     [查看详情]      │
│ Cloud Runner        在线 · 未绑定   │
│                     [开始配对]      │
│                                    │
│ + 扫描二维码添加 Node                │
└────────────────────────────────────┘
```

- 切换 Node：返回 Workspace Node 列表，或从 Node header 打开同级 Node switcher。
- 连接失败：停留在 Workspace/Node 上下文，允许重试或选择其他 Node。
- 退出当前 Node：关闭 App 到 Node 的执行会话并返回 Workspace，不删除绑定。
- 移除此 App 的信任：删除本机 binding，属于 Node 详情中的危险动作。
- 从 Workspace 移除 Node：影响所有成员，只对 owner/admin 展示。

“退出”“解绑”“从 Workspace 移除”必须使用不同文案和确认强度。

## 7. 对话

```text
┌────────────────────────────────────┐
│ ‹ Personal Workspace               │
│ Robin Desktop · 在线       新话题   │
│ [ 对话 ]  [ 任务 ]              ⋯  │
├────────────────────────────────────┤
│                    ┌─────────────┐ │
│                    │ 帮我检查日志 │ │
│                    └─────────────┘ │
│  正在读取日志…                      │
│  找到两个需要处理的问题：……          │
│                                    │
├────────────────────────────────────┤
│ ＋ │ 🎙 和小诺说点什么…        发送 │
└────────────────────────────────────┘
```

- 用户消息立即出现；Agent 正文在同一回复区域流式更新。
- 图片、文件和语音属于 Conversation/Artifact，不创建隐式 Task。
- Conversation 目录属于 Workspace；Robin Desktop 是该 Conversation 的固定内容与执行 Node。
- Approval 与 HumanInteraction 嵌入原始回复，可恢复且只能解决一次。
- 当前 Workspace/Node 始终可见，避免操作目标混淆。
- Node 中断时保留本地草稿和 Workspace 最后投影，提供重连或返回 Workspace；不能在原会话中隐式换 Node。

## 8. 任务

Task Definition 是 Workspace Work。V1 已发布或启用的 Task 必须部署到一个 Node，每个
TaskExecution/Attempt 固定在该 Node。修改 Deployment 只影响未来 Execution。

```text
┌────────────────────────────────────┐
│ ‹ Personal Workspace               │
│ Workspace / 任务     Node: Robin  ＋ │
│ [ 对话 ]  [ 任务 ]              ⋯  │
├────────────────────────────────────┤
│ 待处理  进行中  最近  未开始         │
│                                    │
│ 分析 Jira 新分配工单        待确认   │
│ 已生成建议，需要你的授权             │
│                                    │
│ 检查失败 Pipeline          已完成   │
│ 发现 2 个失败 Job                   │
└────────────────────────────────────┘
```

- Workspace 任务列表跨 Node 展示，可以按部署 Node 过滤。
- 详情、审批、暂停和继续先读取 Workspace Work，再连接实际执行 Node 完成 live control。
- 切换过滤 Node 不改变 Task ID、TaskExecution 历史或 Workspace 归属。
- Node 离线时仍展示稳定定义和已同步历史；live control 明确显示等待重连或不可用。
- Hosted Hub 是否保存明文是部署选择，不能反向改变 Task 的 Workspace 产品归属。

## 9. Workspace 资源与 Node 配置

Workspace 资源页管理：

- Agent、Profile、共享 Model、Skill、MCP 和 Tool Policy；
- Published Spec generation、grant 和默认选择；
- Workspace Draft、validate、impact 和 publish；不提供版本树或 rollback 页面。

Workspace Node detail 管理：

- Node 名称、标签、Workspace 目录和 Deployment intent；
- Node Desired/Applied Generation 与 rollout；
- Secret requirement status、Tool inventory 和本地模型发现；
- active/draining generation、运行状态和诊断；
- 热应用、重启受影响组件和高级 Node service restart。Workspace 本身没有 restart 操作。

UI 必须同时显示“逻辑归属”和“部署位置”，不能把 Workspace resource 与 Node deployment 合并成
一份可双写配置。Node 离线时允许保存和发布 Desired State，并显示“等待 Node 上线”；需要本机
Secret 或 OS 权限的步骤显示为待用户在目标 Node 完成。

## 10. Account、Workspace、Node 设置分层

| 设置 | 页面位置 | 作用域 |
| --- | --- | --- |
| 主题、语言、通知、更新 | Account / App 设置 | 当前 App installation |
| 密码、Account session、Hub | Account | 当前 Account/issuer |
| 成员、资源授权、审计 | Workspace 设置 | 当前 Workspace |
| Agent/LLM/Skill/MCP/Tool Policy | Workspace / 资源 | 当前 Workspace |
| Node 目录与本机偏好 | Workspace / Nodes / Node detail | 当前 WorkspaceNodeEnrollment |
| Resource/Task Deployment、远程共享策略 | Workspace / Resources/Work/Nodes | Workspace Desired State |
| Relay、版本、设备信任、OS 权限 | Node detail / 本机操作 | 当前 Node 或当前 App binding |

每个写操作的标题和确认文案都必须显示实际作用域。

## 11. 登录、注册与新用户引导

Auth 页面只处理 Account：

```text
Hub 地址
登录标识
密码
[登录]

[扫描注册二维码] [恢复密码]
[使用本地 No-Hub 模式]
```

登录成功后进入 Personal Workspace。若没有 Node，App 启动可恢复的 Setup Wizard：添加第一台
Node、发现本地能力、选择本地/云模型、启用默认 Agent、验证并发布，直到第一次真实对话成功。
Workspace 管理和 Account 设置不因向导未完成而被锁住。

## 12. 错误与降级

### Hub 失联

- 保留当前层级和缓存；
- 已建立的 direct Node session 可以继续 Node-local 操作；
- Workspace 管理写操作禁用并允许重试。

### Node 离线

- 只影响 Node 子页面；
- 用户可返回 Workspace、选择其他 Node 或管理帐号；
- 不自动退出 Account，不进入登录页。

### Node 认证失效

- 在 Node scope 内重新认证；
- 失败后提供重新绑定、切换 Node、退出到 Workspace；
- 不映射为 Hub Account 认证失败。

### Relay 中断

- 显示 direct/Relay 和可执行恢复动作；
- 不丢草稿，不产生根路由重定向循环。

## 13. Unicode 与内容呈现

- JSON、SSE、NDJSON 和文本 Artifact 显式按 UTF-8 解码一次。
- UI 不修复 `ä¸­æ–‡` 等 mojibake；transport 必须交付正确 Unicode。
- 中文、Emoji、组合字符和跨 chunk 多字节字符进入自动测试。
- 二进制 Artifact 不经过字符串转换。
- 日期、数字和状态文案由 locale 层格式化。

## 14. 可访问性与返回行为

- breadcrumb、顶部 Chat/Task switcher、Node presence 和连接状态都有 accessibility label/state；
- 颜色不是在线/失败/待确认的唯一表达；
- 触控目标至少 44dp；
- Android Back 严格沿页面父级上行；
- 退出 Node 无需危险确认，解绑和移除 Workspace Node 必须确认；
- Reduce Motion 下关闭非必要启动动画。

## 15. UI 验收场景

1. 新用户登录后没有 Node，仍能管理 Account 和 Workspace。
2. 用户可从 `Account -> Workspace -> 对话/任务` 正常进入，并看到当前 placement Node。
3. App 可默认直接恢复由 Robin Desktop 执行的对话，但返回路径仍是 Workspace -> Account。
4. 用户退出 Robin Desktop 后回到 Workspace，重新进入无需配对。
5. Robin Desktop 离线时 App 回退 Workspace，而不是卡死或回登录。
6. 两个 Workspace 的 Node、Task、Conversation 和配置不会串线。
7. Hub Account 过期与 Node session 过期显示不同错误。
8. Task 和 Conversation 中文、Emoji 经 Relay 显示正确。
