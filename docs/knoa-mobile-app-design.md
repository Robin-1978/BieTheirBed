# Knoa Mobile App 正向架构设计

> 状态：Mobile App 权威目标架构
> 更新日期：2026-08-17
> 适用范围：Hosted Hub、Self-hosted Hub、No-Hub、Android/iOS App
> 设计取向：账户级移动控制台；Hub 控制面常驻；Node 执行上下文可选；高内聚、低耦合；KISS、YAGNI；不保留与目标模型冲突的旧导航兼容层

配套视觉与交互见 [knoa-mobile-ui-design.md](./knoa-mobile-ui-design.md)。跨模块状态所有权以
[knoa-module-architecture.md](./knoa-module-architecture.md) 为准；Workspace、共享资源和 Node
执行权威以 [knoa-workspace-resource-fabric-design.md](./knoa-workspace-resource-fabric-design.md)
为准。

## 1. 架构修订结论

旧 App 将“连接 Node”作为进入应用的根条件：未连接时被重定向到连接页，连接后又把 Chat/Task
页面当成应用根。这使 Hub Account、Workspace、Node directory 和设置被一个 Node 的连接状态
绑架，并产生以下错误体验：

- 未选择 Node 时无法稳定进入设置；
- 进入 Node 后没有“退出当前 Node”的产品语义；
- Node 离线、认证失败或 Relay 失败会锁住整个 App；
- 登录、注册、Workspace 切换、Node 配对和 No-Hub 被混在一个页面；
- `GatewayProvider` 同时承担 App bootstrap、Node selection、Node session、Conversation 和 Agent
  状态，导致任何局部错误都容易升级为全局错误；
- Hub/Workspace/Node 的目标架构已经分离，但 UI 导航仍然沿用单 Node 客户端模型。

目标产品定义调整为：

> Knoa App 是账户级移动控制台。Hub Account 与 Workspace 构成常驻控制面；Node 是当前
> Workspace 下可选择、可退出、可失败的执行上下文。Conversation、Task 和 Node-local 配置
> 需要执行 Node；账户、Workspace、成员、Node 目录和 App 设置不需要当前 Node。

这不是增加一个返回按钮，而是重新定义 App 根状态和模块边界。

## 2. 用户可见核心概念

```text
Identity Issuer / Hub Service
  └── Account
      ├── Membership ──> Workspace A
      │                    ├── Node directory
      │                    ├── shared resource metadata / grants
      │                    └── selected Node? ──> Conversation / Task / Config
      └── Membership ──> Workspace B
                           └── selected Node? ──> Conversation / Task / Config
```

### 2.1 Account

Account 回答“当前以谁登录”，属于 Hub identity issuer。App 可以在目标架构中保存多个 issuer 的
独立登录，但不自动联邦帐号。登出 Account 是显式的全局动作。

### 2.2 Workspace

Workspace 是唯一逻辑租户、成员与共享资源授权边界。登录 Hosted Hub 后至少有一个 Personal
Workspace。切换 Workspace 不等于退出帐号，也不隐式删除任何 Node binding。

### 2.3 Node

Node 是 Workspace 下的执行位置。V1 中它等价于一个物理 Knoa 安装实例；未来 Hosted virtual
Node 仍以普通 Node descriptor 出现在目录中，App 不增加 `if virtual` 的第二套产品路径。

Node 有三种彼此独立的状态：

- Directory state：Hub 是否知道该 Node、是否在线；
- Trust state：当前 App installation 是否持有该 Node 的本地 pinned binding；
- Session state：当前是否已选择并成功建立 direct/Relay 执行会话。

“退出当前 Node”只清除第三种状态。它不撤销设备、不删除 pinned binding、不退出 Workspace。

### 2.4 Active context

App 的活动上下文是：

```text
ActiveContext = Account + Workspace + optional SelectedNode
```

Node 必须是可空字段。所有代码、路由和 UI 都不得用“没有 Node”表示“没有 App”。

## 3. 分层信息架构

Account、Workspace、Node、Conversation/Task 不是并列模块，而是严格父子层级：

```text
Account
  ├── Account / App settings
  └── Workspace
      ├── members / shared resources / grants / workspace settings
      └── Node
          ├── Conversation
          ├── Task / Execution / Approval / Artifact
          └── Node capability / deployment / configuration / diagnostics
```

登录后先进入 Account scope；选择 Workspace 后进入 Workspace scope；选择 Node 后进入 Node scope。
设置也必须按作用域归位，不能作为一个与 Account、Workspace、Node 并列的万能页面。

| Scope | 主页面 | 子页面 |
| --- | --- | --- |
| Account | Workspace 列表、最近上下文 | Account 安全、Hub、App 设置、更新、诊断 |
| Workspace | 概览、Node 目录、资源 | 成员、授权、Workspace 设置、审计 |
| Node | 对话、任务 | 能力、配置、部署、Node 状态与诊断 |

“随时可管理”通过稳定的上行导航实现，而不是把所有层级拍平成五个 Tab。Node 页面顶部必须明确
显示：

```text
Robin / Personal Workspace / Robin Desktop
```

用户可以从任意 Node 页面返回 Workspace，再返回 Account；也可以用 breadcrumb/context sheet
直接切换同级 Workspace 或 Node。退出 Node 后导航回所属 Workspace，不退出帐号。

### 3.1 默认进入位置

领域层级固定，但启动落点是用户偏好。App 可以默认：

- 进入 Account 首页；
- 进入上次 Workspace；
- 推荐：恢复上次 `Account -> Workspace -> Node -> Chat/Task`。

直接恢复 Node 页面时必须重建完整父级导航和 breadcrumb，不能让 Node 页面成为无父节点的应用根。
若默认 Node 离线、已移除或认证失败，回退到所属 Workspace 的 Node 页面；若 Workspace 不再可访问，
再回退到 Account 首页。任何失败都不能回到登录页，除非 Account session 本身失效。

## 4. 路由架构

目标 Expo Router 结构：

```text
app/
├── _layout.tsx                    # providers + root auth boundary
├── (auth)/
│   ├── login.tsx
│   ├── register.tsx
│   └── recover.tsx
├── account/
│   ├── index.tsx                  # Account home + Workspaces
│   ├── security.tsx
│   ├── hub.tsx
│   └── app-settings.tsx
├── workspaces/[workspaceId]/
│   ├── _layout.tsx                # Workspace stack + account access
│   ├── index.tsx                  # Workspace overview
│   ├── nodes.tsx
│   ├── resources.tsx
│   ├── members.tsx
│   ├── settings.tsx
│   └── nodes/[nodeId]/
│       ├── _layout.tsx            # Node stack + breadcrumb + top Chat/Task switcher
│       ├── chat.tsx
│       ├── conversations/...
│       ├── tasks.tsx
│       ├── tasks/...
│       ├── executions/...
│       ├── capabilities.tsx
│       ├── configuration.tsx
│       └── status.tsx
├── (modal)/
│   ├── workspace-switcher.tsx
│   ├── node-switcher.tsx
│   ├── pair-node.tsx
│   └── account-switcher.tsx
└── index.tsx                       # bootstrap only; no Node-based redirect loop
```

路由规则：

1. 未登录 Hub 时，只能进入 Auth stack；No-Hub 使用显式本地 Account/Workspace 进入同一层级。
2. Account、Workspace、Node route 必须携带稳定 ID，不能只依赖全局 active singleton。
3. Node-scoped route 若失去 Node，展示局部 unavailable state，并允许返回 Workspace、切换或重试。
4. Android Back 按 `Node detail -> Node home -> Workspace -> Account -> exit App` 上行，不隐式删除状态。
5. Workspace 切换先关闭旧 Node session，再进入目标 Workspace；默认 Node 恢复是可选偏好。
6. Deep link 到 Conversation/Task 必须验证 Account membership、Workspace 和 owning Node 后再进入。

### 4.1 导航位置约束

App 不使用持久底部导航。对话输入框、附件预览、语音控制、键盘和 safe area 必须独占屏幕底部；
任何导航条都不能放在 composer 下方或与 composer 同层争夺高度。

- Account 使用普通 Stack 页面：Workspace 列表和 Account/App 设置入口；
- Workspace 使用 overview + drill-down：Node、资源、成员、设置都是子页面，不常驻 Tab；
- Node 顶部只保留高频“对话/任务”紧凑切换；
- Node 能力、配置、状态和退出从顶部 Node 菜单进入独立 Stack 页面；
- breadcrumb/back 始终位于顶部；Android Back 沿同一父级语义返回。

这样 Chat 页面底部永远只有 composer，键盘弹出时不需要同时协调 bottom tabs、输入框和 safe area。

## 5. 客户端模块边界

当前 `GatewayProvider` 必须拆分。目标状态树如下：

```text
AppRoot
├── IdentityProvider
│   └── issuer sessions / active account / login / logout / recovery
├── WorkspaceProvider
│   └── memberships / active workspace / role / workspace switch
├── NodeDirectoryProvider
│   └── Hub directory / presence / local trust bindings / pairing
├── NodeSessionProvider
│   └── optional selected Node / direct-or-Relay transport / Node auth / reconnect
├── ServerStateCache
│   └── Node-scoped Conversation / Task / Agent / Config queries
├── TaskReminderProvider
└── PreferenceProvider
    └── theme / locale / last context / auto-connect preference
```

### 5.1 IdentityProvider

拥有：

- Hosted/Self-hosted Hub root URL；
- issuer-scoped Account session；
- Account profile；
- session refresh、失效和登出。

不拥有 Workspace 选择、Node session 或 Gateway client。

### 5.2 WorkspaceProvider

拥有：

- 当前 Account 可访问的 Workspace 列表；
- active Workspace；
- Membership role；
- Workspace 切换状态。

切换 Workspace 时发出明确 context transition，通知 NodeSessionProvider 关闭旧连接。它不直接
建立 Node 会话。

### 5.3 NodeDirectoryProvider

聚合但不混淆两种来源：

- Hub directory/presence：服务端权威；
- local pinned bindings：本机 SecureStore 权威。

通过 `node_id` 形成 presentation projection。Directory 中在线但未绑定的 Node 可以配对；已绑定但
不在当前 Workspace directory 的 Node 只能在 No-Hub/诊断视图展示，不得冒充当前 Workspace Node。

### 5.4 NodeSessionProvider

只拥有一个可空的执行会话：

```text
idle
  -> connecting(node_id)
  -> ready(node_id, client, transport)
  -> offline | auth_error | relay_error
  -> idle
```

它提供：

- `selectNode(nodeId)`；
- `disconnectNode()`；
- `reconnectNode()`；
- `runAuthenticated(operation)`；
- 当前 Node 的 Agent/Conversation session binding。

`disconnectNode()` 必须立即关闭 transport、取消 event subscription、清除短期 Node session 和
selected Node，但保留 Account、Workspace、binding、历史缓存和用户偏好。

### 5.5 ServerStateCache

所有 Node 权威查询必须以完整上下文作为 cache key：

```text
issuer_id / account_id / workspace_id / node_id / resource_kind / resource_id
```

禁止仅以 `task_id`、`session_handle` 或全局 singleton 保存数据，避免切换 Workspace/Node 后串数据。
缓存是体验状态，不覆盖服务端 revision。

## 6. 启动与恢复

```text
App starts
  -> restore Identity session
     -> missing/expired: show Auth stack
     -> valid: show Account scope
  -> resolve preferred landing route
     -> Account home
     -> or accessible last Workspace
     -> or accessible last Workspace + Node + Node page
  -> when restoring Node:
       load Workspace directory and bindings
       connect Node in background
       success -> enter requested Chat/Task page
       failure -> fall back to owning Workspace / Nodes
```

关键约束：

- Account/Workspace 页面可见不等待 Node；
- Account/Workspace 请求失败与 Node 请求失败使用不同 error boundary；
- Node offline 不弹不可关闭的阻塞页；
- 上次 Node 只是偏好，不是强制恢复条件；
- 用户可以关闭“启动时自动连接上次 Node”。

## 7. 核心交互语义

### 7.1 选择 Node

选择 Node 是从 Workspace scope 进入一个 Node child route，并建立 Node session。连接过程中仍保留
Workspace breadcrumb；连接失败停留在 Workspace/Node 上下文，不进入全局错误页。

### 7.2 退出 Node

Node switcher 和 Node 详情都提供“退出当前 Node”：

```text
disconnect transport
  -> cancel subscriptions
  -> clear Node session token
  -> selectedNodeId = null
  -> navigate to owning Workspace / Nodes
```

退出后回到所属 Workspace 的 Node 页面。再次进入同一 Node 不需要重新配对。

### 7.3 切换 Workspace

```text
close current Node session
  -> change activeWorkspaceId
  -> invalidate old Workspace projections
  -> load new directory/resources
  -> optionally restore new Workspace last Node
```

禁止让一个 Workspace 的 Node session 在另一个 Workspace 下继续显示。

### 7.4 Node 删除与解绑

三个动作必须使用不同文案和确认级别：

- 退出当前 Node：可逆，只结束本次执行上下文；
- 移除此 App 的 Node 信任：删除本地 binding，必要时撤销 Node device；
- 从 Workspace 移除 Node：Workspace owner 管理动作，影响其他用户，必须单独授权。

## 8. 控制面与执行面页面

### 8.1 Hub/Account 控制面

始终可用：

- Account profile、密码与 session；
- 当前 Hub issuer 与服务状态；
- Workspace membership；
- App installation、安全和更新。

### 8.2 Workspace 控制面

不依赖当前 Node：

- Workspace 名称、类型、成员和角色；
- Node directory/presence；
- Workspace resource metadata、revision digest、grant 和 deployment observation；
- 邀请、owner 管理及审计。

Hosted Hub V1 不保存 Agent Prompt、Skill/MCP 明文或 Node Secret。若一个编辑动作需要 WorkspaceRegistry
或目标 Node，App 必须显示实际执行位置和可用性，不能让 Hub UI 假装已经热发布。

### 8.3 Node 执行面

需要 Node session：

- Conversation 和 Task；
- Agent/Profile/Runtime 的 Node applied state；
- Node-local model endpoint、Secret、MCP process 和 Tool inventory；
- 配置 Draft/validate/preflight/publish；
- Runtime diagnostics、Artifact 和 execution history。

未来 Workspace 共享资产接入后，App 仍显示“逻辑归属在 Workspace、实际部署在 Node”，不复制两套
资源定义。

## 9. Relay 与 UTF-8 协议边界

### 9.1 已确认的问题

2026-08-17 现场数据确认：Node SQLite 中 Task 标题、目标和 Conversation 中文原文正常；乱码只在
经 Hosted Hub Relay 返回到 App 后出现。当前 Relay transport 将解密后的 HTTP response bytes 直接
传入 React Native `Response(ArrayBuffer)`，运行时可能把 UTF-8 bytes 按单字节字符串解释，形成
`ä¸­æ–‡` 一类 mojibake。Task 与 Conversation 同时受影响，证明问题位于通用 transport adapter，
不属于 Task 或 Chat 领域模型。

### 9.2 正确合同

Relay 层只重组 HTTP bytes，不解释领域 JSON。完成 response 后按权威 `Content-Type` 构造 body：

- `application/json`、`application/*+json`、`text/*`、`text/event-stream`、NDJSON：使用
  `TextDecoder("utf-8", { fatal: true })` 显式且仅解码一次；
- APK、Artifact、图片、音频、压缩包和 `application/octet-stream`：保持原始 bytes；
- 未知类型默认保持 bytes，调用方必须显式选择文本解码；
- 服务端 JSON 响应统一返回 `Content-Type: application/json; charset=utf-8`；
- 不使用 `escape/unescape`、`decodeURIComponent` 或 mojibake 字符替换进行补救。

流式文本必须用同一个 streaming `TextDecoder` 跨 chunk 解码，不能假设一个多字节字符不会跨帧。

### 9.3 必测字符集

Transport contract tests 必须覆盖：

- 简体中文、繁体中文；
- Emoji 和四字节 Unicode；
- 组合音标；
- 多字节字符恰好跨 Relay chunk；
- JSON、SSE/NDJSON 和二进制 Artifact；
- `Content-Length` 以 bytes 而不是 JS string length 校验。

## 10. 安全边界

1. Account token、Node session token 和 Node private key 分开存储与撤销。
2. Hub 登录不能授予 Node Tool 权限；Node binding 不能授予 Workspace 管理权限。
3. Workspace role 在 Hub 控制面校验；Node-local Principal 和 Capability 由 Node 最终校验。
4. Relay 只看 routing metadata 和 ciphertext；App 验证 Hub ticket、pinned Node identity 和消息序列。
5. 切换 Workspace/Node 必须终止旧 event feed，防止跨上下文事件泄漏。
6. App 不保存 Node Secret 明文，不接收 Core local service token。
7. 所有 destructive 管理动作显示实际作用域：本 App、当前 Node 或整个 Workspace。

## 11. 当前实现差距

| 目标 | 当前实现 | 处理 |
| --- | --- | --- |
| 登录后进入 Account/Workspace 层级 | `index` 按 Gateway status 重定向 | 移除 Node-based root redirect |
| Hub/Workspace 独立状态 | `connect.tsx` 局部 state | 提升为 Identity/Workspace providers |
| Node 可空、可退出 | active binding 总会选第一个 | 增加 explicit null selection 与 `disconnectNode()` |
| Account -> Workspace -> Node 层级 | Chat/Task 与管理入口被拍平 | 分层 routes、breadcrumb、顶部 Chat/Task switcher |
| 登录/注册与节点管理分离 | 全部混在 `connect.tsx` | 拆 Auth、Workspace、Node、Pairing 页面 |
| Node 错误局部化 | `GatewayProvider.status=error` 驱动根跳转 | NodeSession error boundary |
| UTF-8 Relay 正确解码 | `Response(ArrayBuffer)` | content-type aware explicit UTF-8 decode |
| cache 完整隔离 | 多处 singleton/active session | 引入 context-scoped keys |

## 12. 实施顺序

### Phase 0：数据正确性

- 修复 Relay 文本 UTF-8 解码；
- 增加中文/Emoji/跨 chunk tests；
- 验证 direct 与 Relay 返回一致。

### Phase 1：分层 Shell 与状态拆分

- 引入 Identity、Workspace、NodeDirectory、NodeSession providers；
- 建立 Account、Workspace、Node 三层 Stack、breadcrumb 和顶部 Chat/Task switcher；
- 删除 Node-based root redirect；
- 实现选择、切换和退出 Node。

### Phase 2：管理页面闭环

- 独立 Auth、Workspace、Node、Account 页面；
- Workspace 成员与 Node directory；
- Node 配对、信任、诊断和 Workspace 移除动作分离；
- 无 Node/离线/认证失败的局部空状态。

### Phase 3：资源与配置呈现

- Workspace resource metadata/grants/deployment observations；
- Node-local Agent/LLM/Skill/MCP/Tool/config applied state；
- 清晰呈现逻辑归属、部署位置和发布状态。

不在本轮为了“通用”引入 Redux、事件总线、微前端或动态页面插件。React Context + 小型领域 hooks
足以表达当前领域状态边界；出现明确性能证据后再引入 query cache library。

## 13. 验收不变量

1. 已登录用户在没有任何 Node 时仍能进入 Account、Workspace、成员、资源和 App 设置。
2. 任一 Node offline/error 不影响切换 Workspace、管理帐号或选择其他 Node。
3. 用户可以随时退出当前 Node，且 binding 与 Hub session 保留。
4. App 可默认恢复上次 Workspace/Node/页面，但父级层级仍完整，恢复失败按 Node -> Workspace -> Account 回退。
5. Chat/Task 永远明确显示当前 Workspace 和 Node。
6. 切换上下文后不会展示旧 Node 的 Conversation、Task、Approval 或 Config。
7. 中文、Emoji 和二进制 Artifact 经 direct 与 Relay 得到字节等价结果。
8. Account logout、Node disconnect、Node unbind、Workspace remove 是四个不同动作。
9. Hosted virtual Node 出现时复用现有 Node 选择和执行模型。
10. App 只是控制面与 Channel，不复制 Hub、Workspace、Task、Agent 或 Approval 状态机。
