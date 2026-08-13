# Knoa Approval Reviewer 设计

## 目标

在现有 Tool Policy、Approval 和 ToolStep 提交边界之间加入可选的智能审批，
复用 Knoa 的 Agent Runtime 接入方式，不耦合 Jira、GitLab、Codex 或具体模型。

```text
ToolStep
  -> deterministic Tool Policy
  -> existing Approval
  -> reviewer_agent (no Tools)
       -> approve | deny | escalate
  -> Platform applies mode and risk ceiling
  -> existing Approval resolve
  -> stale checks and single ToolStep commit
```

## 边界

- `reviewer_agent` 与 `knoa`、`codex` 使用同一个 `AgentRuntime` SPI 和模型
  Provider 配置。
- 它是 Platform 注册的 system Agent，不出现在 App/CLI/TUI 的 Agent 选择中，
  不能绑定普通 Session，也不能创建 Task。
- Reviewer 的 MCP grant 不包含任何 capability，因此 `tools/list` 为空；它不能
  读取外部系统、调用 Shell 或直接解决 Approval。
- Platform 只向 Reviewer 提供当前用户意图、规范化 Tool 名称和参数、effect、
  risk 及有限上下文。Tool 参数和上下文均是不可信数据。
- Reviewer 只返回严格的 `approve | deny | escalate`、简短理由和 rule IDs。
- Platform 是唯一能更新 Approval 状态的组件；执行前仍重新校验 Tool、schema、
  参数、capability、effect 和 risk。

## 模式

- `off`：保持原有人工审批。
- `suggest`：将 Reviewer 建议和理由写入原 Approval，仍等待人类决定。默认用于
  新模型上线和评测。
- `auto`：仅在 Reviewer 输出明确 approve/deny 且 risk 不超过
  `auto_max_risk` 时自动解决。`high` 永远转人工。

超时、模型错误、非法 JSON、信息不足和 `escalate` 均保持 Approval pending。

## YAGNI 决策

- 不新增 Evidence/Jira/GitLab 专用审批协议或表。
- 不新增 Reviewer 自有 Tool、Task、Session 产品模型或公共 API。
- Reviewer 建议复用 Approval 的 reason/resolved_by 审计字段。
- MVP 不实现大小模型级联、confidence 阈值、在线训练或通用规则 DSL。
