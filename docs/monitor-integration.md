# Monitor 接入设计

`/home/robin/ws/Monitor` 已经是一个边界清晰的独立服务：它负责轮询 GitLab/Jira、把结果规范化写入自己的 SQLite，并通过官方 MCP stdio 暴露查询和受保护操作。Knoa 不应复制 Monitor 的插件、轮询器或数据库表。

## 采用的边界

```text
GitLab/Jira -> Monitor poller -> Monitor SQLite
                                  ^
Knoa Agent -> Monitor MCP stdio -+
```

- Monitor poller 独立运行，故障不会阻塞 Knoa 主服务。
- Knoa 只把 Monitor 当作一个 MCP 扩展，自动发现 `monitor.*`、`gitlab.*` 工具。
- `monitor.list_*` 默认只读；GitLab 重试仍保留高风险确认和幂等键。
- Monitor 数据库保持独立，避免把外部系统的观察快照混入 `assistant.db`。
- 首阶段先接入只读查询；确认查询、权限和数据新鲜度稳定后，再开放重试操作。

## 接入方式

优先使用 Knoa 的本地 MCP 包/stdio 配置，让 MCP 进程的工作目录指向 Monitor 项目目录，从而与轮询器共享 `monitor.db`。凭据不复制进 Knoa 数据库，也不写入 MCP manifest；需要开放写操作时，只通过受控的服务环境变量传入 `MONITOR_ACTIONS_ENABLED` 和对应 provider 凭据。

Monitor 的 dashboard 保持独立，只读展示同一份 SQLite；它不是 APP 的数据接口。APP 若需要展示 Monitor 状态，应由 Agent 通过 MCP 查询后生成会话或任务结果，不直接访问 Monitor 数据库。

## 不做的事情

- 不把 Monitor 的轮询线程合并进 Knoa 服务。
- 不让 APP 直接连接 GitLab/Jira 或 Monitor SQLite。
- 不默认开放 `retry_pipeline`、`retry_job` 等外部副作用工具。
- 不为 Monitor 另造一套 Push 通道；任务结果仍走现有 APP 内提醒机制。

这样接入的核心改动只在 MCP 配置和部署目录，Knoa 核心代码无需引入 Monitor 的 provider 类型；后续新增 provider 也不需要修改 Agent runtime。
