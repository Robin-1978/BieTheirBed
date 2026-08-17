# Knoa Product Forward Blueprint

> 更新：2026-08-17

## Target

```text
Account -> Workspace -> Node -> NodeAgent / Conversation / Task / local capabilities
                     \-> shared LLM/MCP directory and grants
```

Knoa 是一个 local-first、多 Node 的个人 Agent 产品。Hub 提供身份和协调，Node 提供执行与数据，App 提供
统一管理。V1 首先闭环 Company Node + Home Node：Company 运行 Qwen/Jira/GitLab，Home 运行自己的 Knoa
Agent 与任务，只按需共享 Company Qwen。

## Delivery order

1. Account/Workspace/Node 稳定导航；
2. 单一 NodeAgent 配置与热生效；
3. Node-owned Conversation/Task；
4. Workspace LLM/MCP directory 与 Grant；
5. signed direct candidate、direct-first、Relay fallback；
6. App 新用户 onboarding、离线/切换、配置与自更新；
7. 可观测性、备份与 Hosted Hub 运营。

## Guardrails

- 一个事实一个写权威；
- 不兼容旧产品模型时直接删除旧模型；
- 不牺牲已部署 Account/Workspace 数据的数据库安全迁移；
- Package、revision graph、Agent sharing、自动 placement 与 Marketplace 遵循 YAGNI；
- 所有外部副作用 fail closed，并可审批、停止、审计。
