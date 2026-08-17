# Knoa Task 产品设计

> 状态：权威设计
> 更新：2026-08-17

## 1. 所有权

Task 属于创建它的 Node，不属于 Workspace：

```text
NodeTask
├── task_id / title / goal / state
├── node_agent_id
├── launch_policy / trigger
├── required_resource_refs
├── notification_policy
└── TaskExecution
    └── Attempt
        └── Agent Invocation
```

用户先进入目标 Node，再创建 Task，因此不需要 `TaskDeployment`。Task 的上线、暂停、恢复、执行、Stop、
审批与历史都在同一 Node 完成。

## 2. 依赖

Task 可依赖本 Node 的 Agent、Tool、Model 和 MCP，也可依赖 Workspace 授权给本 Node 的远程
ModelEndpoint/MCPEndpoint。依赖解析必须在启动时给出明确的 target Node、Grant、健康和失败策略。

Jira/GitLab Task 应创建在 Company Node；家庭文件或桌面 Task 应创建在 Home Node。Home Task 可以使用
获授权的 Company Qwen，但这不改变 Task 的 Home Node 所有权。

## 3. Workspace 投影

Node 向 Workspace 上报只读投影，用于多 Node 概览、通知和从 App 定位权威 Node。投影不包含完整 goal、
trigger Secret、执行输入、ChatTurn 或 Artifact 内容，不能从 App 写回。

## 4. 生命周期

```text
draft/active <-> paused -> archived
TaskExecution: queued -> running -> waiting_approval -> completed | failed | stopped
```

Trigger 由拥有 Task 的 Node 调度。Node 离线时本机 trigger 不执行，重新上线后按 Task 明确的 misfire
策略处理；Hub 不替代 Node 执行 Task。

## 5. App

Task 列表与编辑入口位于 `Workspace -> Node -> Tasks`。Workspace 可提供“跨 Node 活动”只读页，点击投影
时先连接其 Node，再打开 Task。Node 离线时只展示最后状态与离线原因。

## 6. 不变量

1. Task 只有 Node 一个写权威；
2. App 不创建 Workspace Task Resource/Deployment；
3. Task Agent 必须是同 Node NodeAgent；
4. 远程依赖仅限获授权 Model/MCP；
5. Stop 与 Approval 必须到权威 Node；
6. 通知是状态输出，不成为执行权威。
