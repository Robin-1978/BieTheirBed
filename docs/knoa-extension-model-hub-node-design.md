# Knoa 扩展、模型与 Hub/Node 边界

> 状态：权威补充设计
> 更新：2026-08-17

## 1. 扩展

普通产品对象只有 Skill 内容和 MCP 服务。来源可以是本地目录、受信归档或远程 HTTPS，但 PackageStore、
content digest 与安装 artifact 只是内部完整性机制，不进入普通导航。

- Skill：数据与说明内容，检查后同步到 Node；
- local MCP：Node 管理的本地服务；
- remote MCP：Node 连接的 HTTPS 服务；
- 任意原生代码不能以内嵌 Skill 名义取得执行权。

## 2. 模型

```text
Provider configuration + Node-local Secret
-> Model alias
-> NodeAgent model_binding
-> optional ModelEndpoint sharing
```

Knoa Agent 使用 Platform 管理的模型绑定；Codex Agent 使用 Codex 自己的模型配置。Model Provider 不是
可执行 package，云 API 配置是 Node-local 实例。

## 3. Hub/Node

Hub 管 Account、Workspace、Node directory、共享 LLM/MCP 目录、Grant、ticket 和只读投影。Node 管 Agent、
Work、Endpoint、Tool、Secret 与执行。Relay 不解密业务数据。

## 4. 导入与应用

```text
source
-> inspect provenance/schema/capabilities
-> immutable internal snapshot when needed
-> Config Draft
-> preflight
-> publish to current Node
```

用户看到“添加 Skill/MCP”，不看到“安装 package”。高级诊断可展示来源、版本、签名和 digest。

## 5. 暂不实现

- 公共 Marketplace、评分、付费；
- 通用依赖求解器；
- 跨账户扩展共享；
- Runtime plugin 下载并进程内加载；
- Agent sharing 与 Remote Tool。
