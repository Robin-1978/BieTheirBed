# Knoa Mobile UI 与交互设计

> 状态：与 Mobile App 权威架构对齐的目标 UI
> 更新日期：2026-08-17
> 原则：Account -> Workspace -> Node -> Conversation/Task；层级固定，默认落点可配置，任何页面都有明确上行路径。

架构、状态所有权和实施顺序见
[knoa-mobile-app-design.md](./knoa-mobile-app-design.md)。本文只定义用户看到的导航、页面和行为。

## 1. 产品导航模型

App 不是五个并列功能的集合，而是三个逐层进入的工作范围：

```text
Account
  -> Workspace
    -> Node
      -> 对话
      -> 任务
      -> 能力与配置
      -> Node 状态
```

每一层拥有自己的页面和设置：

| 层级 | 核心页面 | 设置作用域 |
| --- | --- | --- |
| Account | Workspace 列表、最近使用 | 帐号安全、Hub、App 外观/语言/更新 |
| Workspace | 概览、Node、资源、成员 | Workspace 名称、成员、授权、审计 |
| Node | 对话、任务、能力、状态 | Agent/LLM/Skill/MCP/Tool、Node 连接与诊断 |

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

- Node 卡片/入口：目录、presence、绑定、配对和进入 Node；
- 资源入口：Workspace resource metadata、grant、deployment observation；
- 成员入口：成员、角色和邀请；
- 设置入口：Workspace 属性、授权和审计。

顶部 `‹ Robin / Personal Workspace` 可返回 Account 或切换 Workspace。没有 Node 时仍可完整使用这些
页面。

## 4. Node Shell

选择并连接 Node 后进入 Node scope：

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

Node scope 只把两个最高频子资源放在顶部紧凑切换器：

- 对话：Conversation、附件、语音、Approval、Interaction；
- 任务：Task、Execution、Automation、结果和确认；

顶部 `⋯` Node 菜单进入低频管理页面：

- 能力与配置：Agent、LLM、Skill、MCP、Tool 和配置发布；
- Node 状态：连接、版本、Relay/direct、诊断和审计；
- 切换 Node、退出 Node。

顶部第一行始终提供返回 Workspace；Account 头像保持可达。Node 页面不能成为没有父级的 App 根。

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
    -> Robin Desktop
      -> 对话
```

即使视觉上直接显示对话，导航栈仍必须包含 Workspace 和 Account。用户点击返回时按以下顺序上行：

```text
Conversation detail -> Node Chat -> Workspace -> Account -> exit App
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
- Approval 与 HumanInteraction 嵌入原始回复，可恢复且只能解决一次。
- 当前 Workspace/Node 始终可见，避免操作目标混淆。
- Node 中断时保留草稿和历史，提供重连、切换 Node 和返回 Workspace。

## 8. 任务

任务是 Node 的子资源。V1 每个 Task 和 Execution 都有明确 owning Node。

```text
┌────────────────────────────────────┐
│ ‹ Personal Workspace               │
│ Robin Desktop / 任务            ＋  │
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

- 列表、详情、审批、暂停和继续都路由到 owning Node。
- 切换 Node 后先清空旧 projection，再加载新 Node 数据，不能短暂串线。
- Workspace 未来可以提供跨 Node 非敏感摘要，但任务事实不复制到 Hub。
- Node 离线时只能显示本地缓存/Hub 摘要，不假装可以执行控制动作。

## 9. 能力与配置

Node 能力页只管理当前 Node applied state：

- Agent、Profile、Runtime；
- LLM provider、model binding 和 Node Secret status；
- Skill、MCP、Tool inventory；
- Draft、validate、preflight、publish、rollback；
- active/draining generation。

Workspace 资源页管理逻辑资源、grant 和 deployment intent。UI 必须同时显示“逻辑归属”和“部署
位置”，不能把 Workspace resource 与 Node deployment 合并成一份可双写配置。

## 10. Account、Workspace、Node 设置分层

| 设置 | 页面位置 | 作用域 |
| --- | --- | --- |
| 主题、语言、通知、更新 | Account / App 设置 | 当前 App installation |
| 密码、Account session、Hub | Account | 当前 Account/issuer |
| 成员、资源授权、审计 | Workspace 设置 | 当前 Workspace |
| Agent/LLM/Skill/MCP/Tool | Node / 能力 | 当前 Node |
| Relay、版本、设备信任 | Node / 状态 | 当前 Node 或当前 App binding |

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

登录成功后进入 Personal Workspace。若没有 Node，Workspace Node 页显示“添加第一台 Node”，但不
阻塞成员、资源、Workspace 设置或 Account 设置。

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
2. 用户可从 `Account -> Workspace -> Robin Desktop -> 对话/任务` 正常进入。
3. App 可默认直接恢复 Robin Desktop 对话，但返回路径仍是 Workspace -> Account。
4. 用户退出 Robin Desktop 后回到 Workspace，重新进入无需配对。
5. Robin Desktop 离线时 App 回退 Workspace，而不是卡死或回登录。
6. 两个 Workspace 的 Node、Task、Conversation 和配置不会串线。
7. Hub Account 过期与 Node session 过期显示不同错误。
8. Task 和 Conversation 中文、Emoji 经 Relay 显示正确。
