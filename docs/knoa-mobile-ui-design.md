# Knoa Mobile UI 设计

> 更新：2026-08-17

## 1. 导航

顶部栏使用 icon 表达 Account、Workspace、Node、Conversation/Task 当前层级；输入页面不使用固定底部导航，
避免与会话输入框冲突。所有 Node 页面提供返回 Workspace 的明确动作。

```text
[<] [Workspace icon] Personal Workspace
    [Node icon] Robin Desktop · Direct
```

## 2. Workspace 页面

首屏展示 Node 列表与在线状态，其次是“共享服务、Node 管理、成员”。Conversation/Task 不与这些并列；
跨 Node 活动仅作为只读次级入口。

## 3. Node Home

Node Home 是工作入口：

- 对话；
- 任务；
- Agent 与能力；
- 模型与共享；
- 系统配置；
- 连接状态和 App 更新。

每个页面都显示当前 Node，防止用户误以为在修改 Workspace 公共 Agent 或另一台电脑。

## 4. 资源语言

普通用户术语：Agent、模型、MCP 服务、Skill 内容、Tool、Secret、共享、Node。内部 package、digest、
generation 只在高级诊断中出现；revision 在 UI 表达为“草稿/已发布/已生效”。

## 5. 离线与错误

离线 Node 卡片仍可查看最后状态，但进入按钮解释“Node 离线”。direct 失败且 Relay 成功时只展示 Relay；
两者均失败时给出两个路径的简短诊断并允许返回 Workspace，不能全屏永久 loading。
