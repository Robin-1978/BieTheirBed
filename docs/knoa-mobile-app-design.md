# Knoa Mobile App 正向设计

> 状态：App 产品与导航权威设计
> 更新：2026-08-17

## 1. 信息架构

```text
Account
└── Workspace
    ├── Nodes
    │   └── Node Home
    │       ├── Agents
    │       ├── Conversations
    │       ├── Tasks
    │       ├── Models / MCP
    │       ├── Skills / Tools / Secrets
    │       └── Node Settings
    ├── Shared LLM/MCP Services
    ├── Members
    └── Cross-node Activity (read-only)
```

底部不放与输入框竞争空间的全局导航。页面顶部使用 icon + title 表达当前 Account/Workspace/Node，并提供
清晰返回。用户可配置默认进入 Account、Workspace 或上次使用位置，但这只是落点偏好。

## 2. 新用户流程

```text
安装 App
-> 注册/登录 Hosted Hub
-> 创建或进入 Personal Workspace
-> 显示“还没有 Node”
-> 在电脑安装 Knoa Node 并 Enrollment
-> App 扫描 Node pairing QR
-> 进入 Node Home
-> 配置 NodeAgent 与 Model
-> 开始 Conversation 或创建 Task
```

Hub 登录成功不要求 Node 在线。没有 Node 时 App 仍可管理帐号、Workspace、成员、添加 Node 和检查更新。

## 3. 老用户流程

- 启动时恢复 Hub session 与 Workspace；
- 若默认落点是 Node/Conversation，先确认 binding 与在线状态；
- 连接失败则回到 Workspace 并显示原因，不卡死；
- 可随时切换 Workspace 或 Node；
- Node 配置必须明确是“当前 Node”，Workspace 页面只配置共享 LLM/MCP 目录与 Grant。

## 4. 页面职责

| 页面 | 职责 |
| --- | --- |
| Account | 身份、Workspace 列表、App 设置、退出 |
| Workspace | Node 概览、共享服务、成员、只读活动 |
| Node Home | 当前 Node 工作与配置入口、连接路径、退出 Node |
| Conversations | 当前 Node 会话 |
| Tasks | 当前 Node 任务 |
| Agent/Capability | 当前 NodeAgent、Skill/MCP/Tool 状态 |
| Model Center | 当前 Node 本机/云模型与可选 Workspace 共享 |
| System Config | 当前 Node 的日常选择、绑定、状态、发布和 Node Console 快捷入口 |

普通 UI 不出现 PackageStore、RuntimeSpec、AgentProfile 或 AgentDefinition。

## 5. 连接

App 先从 Hub Node directory 获取签名 direct candidate，再执行有界 direct 尝试，失败后 Relay。UI 只显示
当前 `Direct` 或 `Relay` 和诊断详情，不要求普通用户选择 transport。

Node 离线仅影响该 Node 的 Conversation、Task 和配置操作。Workspace 投影仍可查看；选择其他在线 Node
必须可用。

## 6. 配置体验

配置不是 YAML 编辑器外壳。App 主要提供：

- NodeAgent 编辑；
- Model 选择、Agent 绑定与共享状态；
- Skill/MCP 状态与常用开关；
- Policy 与审批；
- 校验结果、影响范围、是否需要组件/Node 重启；
- 发布和应用状态。

LLM endpoint、API Key、MCP command、本地路径、批量导入/导出和深度诊断放在 Node Console，避免在手机上
输入长 Secret 或复杂路径。两个客户端必须调用同一 ConfigurationService。

## 7. 中文与状态

- API JSON、SSE、SQLite text 与日志 boundary 均为 UTF-8；
- App 不按字节截断 Unicode 字符串；
- loading、empty、offline、permission denied、update required 与 partial failure 都有独立状态；
- Stop、审批、重连和 Node 切换必须可取消、幂等且可观察。
