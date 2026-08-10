# 服务器巨量源码文件治理

## 目标

按业务边界拆分服务器代码，使一次功能修改只触碰一个领域模块，并让测试能够针对该领域独立运行。拆分不改变 API、数据库语义或运行时行为，也不保留旧接口转发层。

## 当前基线

| 文件 | 行数 | 判断 |
| --- | ---: | --- |
| `tasks/repository.py` | 3370 | 必须拆；数据库建表、Task 定义、Execution、Attempt、工具步骤、审批和状态机混在一个类中 |
| `channels/feishu.py` | 2885 | 必须拆；传输、卡片渲染、会话呈现和后台任务通知混在一起 |
| `gateway/adapter.py` | 1692 | 必须拆；认证、会话、任务、Artifact、更新和 SSE 路由集中在一个适配器中 |
| `service/core_server.py` | 1596 | 必须拆；认证和所有 Core 命令分发集中在一个文件中 |
| `service/core_api.py` | 1377 | 暂不按行数拆；内容主要是协议模型，保持单一协议入口更利于生成 Schema |
| `service/core_client.py` | 1295 | 在服务端命令拆分后按同样领域拆分 |

自动生成的 App OpenAPI Schema 不纳入手写源码行数治理。

## 实施结果（2026-08-11）

本轮已完成无双实现拆分：

| 组合入口 | 拆分后行数 | 结果 |
| --- | ---: | --- |
| `tasks/repository.py` | 434 | Schema、运行时、定义、执行和工具仓储已分离 |
| `channels/feishu.py` | 374 | 只保留 Channel 生命周期与依赖装配 |
| `gateway/adapter.py` | 362 | 路由、SSE 和 HTTP 工具已分离 |
| `service/core_server.py` | 593 | 按 Conversation、Task、Artifact、Automation 分发 |
| `service/core_client.py` | 约 1040 | Artifact 与 Automation 客户端操作已抽离 |

`service/core_api.py` 继续作为声明性的单一协议入口；生成的 App Schema 继续不参与
手写业务文件行数约束。

## 正向模块边界

### Task 持久化

- `tasks/schema.py`：建表、索引、精确 Schema 校验。
- `tasks/runtime_repository.py`：运行时 Task、Attempt、事件和领取执行。
- `tasks/definition_repository.py`：用户可编辑 Task 定义和启动绑定。
- `tasks/execution_repository.py`：TaskExecution 查询、删除、重跑关联。
- `tasks/tool_repository.py`：工具步骤、审批和 outcome-unknown 恢复。
- `tasks/repository.py`：只组合上述仓储，不再实现业务 SQL。

### Gateway

- `gateway/routes/conversations.py`
- `gateway/routes/tasks.py`
- `gateway/routes/artifacts.py`
- `gateway/routes/device.py`
- `gateway/streaming.py`
- `gateway/http.py`

`SecureGatewayAdapter` 只负责生命周期、依赖装配和路由注册。

### Core 服务

- `service/core_auth.py`：Core 认证策略；本轮已完成第一步抽离。
- `service/core_conversation_commands.py`
- `service/core_task_commands.py`
- `service/core_artifact_commands.py`
- `service/core_automation_commands.py`
- `service/core_server.py`：连接生命周期、并发订阅和命令路由。

### Core 客户端

- `service/core_client_artifacts.py`：Artifact 上传、下载和转写；
- `service/core_client_automation.py`：Schedule 与 Trigger 操作；
- `service/core_client.py`：连接、认证、订阅以及 Conversation/Task 主协议操作。

### 飞书渠道

- `channels/feishu_transport.py`
- `channels/feishu_cards.py`
- `channels/feishu_conversation.py`
- `channels/feishu_tasks.py`
- `channels/feishu.py`：Channel 生命周期和依赖装配。

## 拆分顺序

1. 先抽离无状态工具与认证代码。
2. 再抽离只读查询，使用现有测试证明结果完全一致。
3. 然后抽离写入事务和状态机，每次只移动一个聚合边界。
4. 最后删除组合层中的旧实现，禁止双实现和兼容代理。

每一步都必须通过全量测试、真实 CLI、会话、审批、Task 和 Artifact 冒烟验证。功能修复与大规模代码搬迁分开提交，方便回退和定位回归。

## 完成标准

- 手写业务文件原则上不超过 1200 行。
- 单个类不同时拥有两个以上聚合的写事务。
- 路由层不直接实现业务状态机。
- 协议模型、生成文件和大段声明性常量按职责评估，不机械追求行数。
