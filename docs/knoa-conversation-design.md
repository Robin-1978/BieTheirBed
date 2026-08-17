# Knoa Conversation 设计

> 状态：权威设计
> 更新：2026-08-17

## 1. 所有权

Conversation 属于一个明确 Node：

```text
NodeConversation
├── conversation_id
├── node_agent_id
├── ChatTurn
│   └── Invocation
├── Approval / Interaction
└── Artifact references
```

Conversation 正文、流式事件、Runtime session binding、审批和 Stop 状态只在该 Node 持久化。Workspace 只
保存最小只读投影用于跨 Node 导航。

## 2. App 行为

路径为 `Account -> Workspace -> Node -> Conversations -> Conversation`。默认落点可以是上次 Conversation，
但顶部始终能看到 Workspace/Node 上下文并退出 Node。

Node 未连接时不能新建 Turn。Node 离线时显示最后投影与“连接 Node”动作，不能无限 loading 或阻断
Account/Workspace 页面。

## 3. Stop 与审批

Stop 必须在 UI 立即进入 stopping，并向权威 Node 发出 idempotent interrupt；无论当前是在模型流、Tool、
MCP 还是等待审批，都必须有可观察结果。等待审批时 Stop 表示取消当前 Invocation，而不是无效按钮。

Approval resolution、user input 与 steer 都绑定 conversation/session/turn/invocation identity，切换 Node 后
不得误投递。

## 4. 投影

Workspace Conversation projection 可包含 title、state、last summary、approval summary、Node、更新时间，
不包含消息正文。投影事件必须有递增 sequence 和 source digest，Hub 只接受同 Node 的新版本。

## 5. 不变量

1. Conversation 不属于 Workspace Work 聚合；
2. Workspace 投影不可编辑；
3. 一个 Turn 只在绑定 Node 执行；
4. Node 切换不会迁移 Runtime session；
5. 中文文本从 Node 到 App 全链路使用 UTF-8 JSON，不做隐式 locale 编解码；
6. reconnect 使用 session reconcile，不重复提交已接受 Turn。
