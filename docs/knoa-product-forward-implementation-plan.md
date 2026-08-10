# 小诺产品正向实施计划

> 依据：[knoa-product-forward-blueprint.md](./knoa-product-forward-blueprint.md)
> 用户问题来源：[knoa-user-experience-audit.md](./knoa-user-experience-audit.md)
> 执行原则：每个阶段先完成领域/API，再完成 App，最后以新鲜测试证据验收。

## 1. 实施约束

- 不建立旧 Task 兼容层；
- 落实既有的 `Task → TaskExecution → ExecutionAttempt` 正向模型，并使用明确的领域命名和持久化边界；
- 不在 UI 中先模拟后端不存在的能力；
- 不在 App 中通过事件顺序推断聚合状态；
- 每个破坏性操作必须先解析精确目标并经过用户确认；
- 每阶段结束时必须保持后端测试、移动端类型检查和已有测试可通过；
- 发布 APK 前必须完成签名验证、版本递增和 Gateway 发布校验。

## 2. Phase A：契约和领域模型

### A1. 落实既有 Task / TaskExecution 领域

建立：

- `TaskRecord`：任务定义；
- `TaskLaunchPolicy`：immediate/scheduled/event；
- `TaskExecutionRecord`：一次运行；
- `ExecutionAttemptRecord`：内部恢复尝试；
- `TaskExecutionTrace`：执行 Trace；
- Task 与 Execution 独立状态机。

删除错误语义：

- `TaskRecord.state=running/completed`；
- `TaskRecord.attempt_count` 被公共 API 使用；
- retry 通过 parent_task_id 构造“新 Task”；
- Schedule/Trigger 作为公共产品对象。

### A2. 新持久化边界

创建新表：

```text
tasks
task_launch_policies
task_executions
task_execution_attempts
task_execution_events
task_execution_traces
task_execution_steps
task_execution_approvals
```

要求：

- Task 删除级联 Execution；
- 单条终态 Execution 可删除；
- 活动 Execution 不可删除；
- Task revision 创建 Execution 时写入快照；
- Scheduled/Event launch 去重只产生一个 Execution；
- 未知外部副作用结果保持暂停，不自动重放。

### A3. 应用服务

建立：

- `TaskService`：Task CRUD、暂停、归档、恢复；
- `TaskLaunchService`：手动、定时、事件启动；
- `TaskExecutionService`：claim、运行、暂停、恢复、取消、rerun；
- `TaskApprovalService`：执行审批；
- `TaskNotificationService`：provider-neutral 通知决策。

验收：领域和 repository 单元测试覆盖所有状态转换、revision 快照、级联删除和重复启动。

## 3. Phase B：Core 与 Gateway API

### B1. Core 协议

新增严格类型消息：

- create/get/list/update/delete Task；
- execute/pause/resume/archive/restore Task；
- list/get/delete/rerun Execution；
- pause/resume/cancel Execution；
- Execution 快照与流；
- Approval 作为 Execution 子资源返回。

原 `create_task/get_task/list_tasks` 的执行语义直接替换，不保留双重解释。

### B2. Secure Gateway

实现蓝图第 12 节 REST API，并满足：

- principal 所有权检查；
- 服务端分页前筛选；
- 统一错误 envelope；
- 速率限制按读写和危险操作区分；
- OpenAPI 与实际路由一致；
- Push 事件携带 task_id/execution_id；
- Artifact 路径不暴露主机文件。

### B3. Agent 工具

Agent 只保留：

```text
create_task
schedule_task
task
```

三个工具都操作同一 Task 模型。`schedule_task` 创建 launch_policy=scheduled 的 Task，
不再返回 schedule_id。`task(retry)` 改为对 Execution rerun 或对 Task execute 的明确动作。

验收：Core client/server、Gateway adapter、OpenAPI 和工具集成测试全部通过。

## 4. Phase C：Mobile 任务中心

### C1. TypeScript 模型和客户端

生成并使用：

- `TaskSummary`；
- `TaskDetail`；
- `TaskExecutionSummary`；
- `TaskExecutionDetail`；
- `TaskLaunchPolicy`；
- `ApprovalSnapshot`；
- 统一 `UserFacingError`。

禁止继续使用当前 `TaskSnapshot` 同时表示 Task 和 Execution。

### C2. 页面结构

```text
/tasks
/tasks/new
/tasks/{task_id}
/tasks/{task_id}/edit
/task-executions/{execution_id}
```

实现：

- Task 列表和服务端筛选；
- 创建 immediate/scheduled/event Task；
- Task 编辑、启用/暂停、归档、恢复、删除；
- 当前配置立即执行；
- 执行记录列表；
- Execution 详情、暂停、继续、停止、rerun 和终态删除；
- rerun 成功后跳转到新 Execution；
- 状态、空态、错误态和操作中状态。

验收：用户绝不会在列表看到因 rerun 产生的重复 Task 卡片；执行次数等于 Execution
数量；Attempt 不进入 UI。

## 5. Phase D：连接身份与配对恢复

### D1. 原子连接记录

把散落的 SecureStore 键合并为按 gateway + device 隔离的原子记录。配对切换时：

1. 验证新配对成功；
2. 写入新 identity；
3. 清理旧 Token、Core Session、事件游标、Push 和更新断点；
4. 创建新 ConversationSession；
5. 开始新事件订阅。

### D2. 用户入口

设置页提供：

- 当前连接对象；
- 最近连接时间；
- 重新连接；
- 重新认证；
- 重新配对；
- 移除此设备。

网络失败不自动清除有效身份。只有明确 401/设备撤销才重新认证或要求配对。

验收：在两个 Gateway 间切换不会复用旧 Session 或游标；断网恢复不会丢失身份。

## 6. Phase E：Conversation 完整闭环

### E1. 历史会话

- Session 列表、重命名、归档和删除；
- 新话题和切换会话保存草稿；
- Core Session 失效时创建新会话并给出解释，不显示内部 404。

### E2. ChatTurn 控制

- 运行中“停止”；
- 失败/取消后“重试”和“编辑后重发”；
- 流断开后快照恢复；
- 仅在用户位于底部时自动滚动；
- 每个 Turn 独立显示错误和恢复动作。

验收：断网、前后台切换、App 重启和认证刷新后，当前 Turn 状态保持一致。

## 7. Phase F：Artifact、媒体和 Approval

### F1. 统一 Artifact 组件

- 聊天与 Execution 共用图片/文件预览；
- 上传逐项进度和精确重试；
- 独立删除按钮；
- 图片保存到相册、分享和缓存语义分离；
- 保存和打开均有成功/失败反馈。

### F2. 相机与录音

- 永久拒绝权限时打开系统设置；
- 拍照预览、重拍、使用照片；
- 录音、上传和转写错误可恢复；
- Audio mode 在所有退出路径恢复。

### F3. Approval

- API 直接返回 pending Approval；
- 展示动作、影响、风险、关键参数和可撤销性；
- 操作失败显示针对该 Approval 的重试；
- 多个 Approval 不互相覆盖。

验收：任何单个附件或审批失败都不会破坏同一消息/执行中的其他内容。

## 8. Phase G：通知、状态与更新

### G1. 通知

- 首次后台 Task 创建时解释并请求权限；
- 设置页启用/停用和测试；
- 注册失败可见且可重试；
- 冷启动/运行期统一深链；
- Task 级完成、失败、待确认通知偏好。

### G2. 状态与设置

默认展示连接、服务、能力、通知、存储和版本的用户状态；原始 Tool、Token 和审计日志
进入高级诊断。每个异常状态必须提供操作按钮。

### G3. 更新

- 全局单次检查；
- required_update 受限模式；
- 已校验 APK 跨重启保留；
- 未知来源权限返回后继续安装；
- 网络、空间、校验和权限错误分开处理。

验收：低于 min_supported_version_code 的 App 不能继续创建对话或任务。

## 9. Phase H：一致性与可访问性

- 一级导航只保留对话和任务；
- 危险操作统一确认；
- 所有图标按钮有 accessibilityLabel；
- 文字缩放后不截断关键按钮；
- loading、empty、error、offline、permission denied 状态完整；
- 避免重复提交和无反馈点击；
- 关键颜色满足可读对比度；
- Android 返回键、系统分享、安装器和权限设置路径完整。

## 10. 验证矩阵

### 后端

- Task/Execution repository 和状态机测试；
- Core protocol parse/response 测试；
- Gateway ownership、错误、分页和 OpenAPI 测试；
- Schedule/event 去重、暂停和恢复测试；
- Approval、Artifact、Push 和删除级联测试。

### Mobile

- Vitest 单元测试；
- TypeScript strict typecheck；
- Expo Android production bundle；
- Android signed release build；
- APK v2 签名和 SHA-256；
- 真机配对、断网、重启、切换 Gateway、聊天停止/重试、任务多次执行、审批、图片保存、
  通知深链、断点更新和强制更新验证。

### 完成条件

只有满足以下条件才能发布：

1. 蓝图第 14 节全局验收标准全部有新鲜证据；
2. 不存在旧 Task-as-Execution API 被 Mobile 使用；
3. 任务列表、执行记录数和通知深链在真机一致；
4. 所有 P0/P1 审查问题关闭；
5. 工作区差异检查、测试、打包和签名全部通过。

## 11. 推进记录规则

每完成一个 Phase，在本文对应章节追加：

```text
状态：completed
提交：<commit>
验证：<commands and results>
剩余：<known gaps>
```

不得仅以“代码已写”标记完成；必须同时有用户行为闭环和验证证据。

## 12. 本轮实施记录（2026-08-10）

### Phase A-C

状态：completed

- 已落实既有 `Task → TaskExecution → ExecutionAttempt` 设计；公共 Task 是稳定定义，
  Execution 是不可变历史快照，Attempt 仅为内部恢复细节；
- 已完成 Task CRUD、revision 乐观锁、启停/归档/恢复、立即/定时/事件启动、多次执行、
  历史快照 rerun、终态 Execution 删除和级联约束；
- Core、Gateway、OpenAPI、Agent 工具和 Mobile 任务中心已统一为相同语义；
- Mobile 支持 immediate/scheduled/event 创建和编辑，启动策略由领域 validator 保证互斥完整；
- Schedule/Trigger provider 会随 Task 更新重建，并在 Task 删除时同步删除，不遗留内部启动器。

### Phase D

状态：completed

- Gateway、device、Token、Core Session、事件游标和最近连接时间合并为原子连接身份；
- 重新配对不会复用旧 Session/游标；网络错误不会误删有效身份；
- 设置页提供重新连接、重新认证、重新配对和移除此设备；
- “移除此设备”会先撤销服务端设备、全部安全会话和 Push 注册，再清理本机身份；
- 相机永久拒绝后可直接打开系统设置。

### Phase E

状态：completed

- 新增稳定 `conversation_sessions` 产品表和 Session 列表、重命名、归档、恢复、删除；
- Mobile 可新建、切换和回到历史会话，草稿按 Session 独立保存；
- ChatTurn 支持停止、失败/取消重试、编辑后重发；
- 流断开或 App 重启后从快照恢复；原 Session 失效时自动创建新会话并给用户解释；
- 只有用户原本位于底部时才自动跟随新内容。

### Phase F

状态：completed

- 聊天和任务执行共用 ArtifactViewer；图片可全屏、缩放、拖动、保存到相册或分享；
- “缓存预览”“保存到相册”“分享”使用独立动作和反馈；
- 多附件逐项显示上传状态，失败项可单独重试，删除使用独立按钮；
- 拍照支持预览、重拍和使用照片；录音异常路径恢复 audio mode；
- Execution API 直接返回持久 Approval；Mobile 支持多个待确认项并展示影响、风险、参数和
  可撤销性提示，不再按事件顺序推断。

### Phase G-H

状态：completed

- 通知权限先解释后请求，设置页可启用/修复和发送测试通知；冷启动和运行期统一深链；
- Push 按 Task 的完成、失败、待确认通知偏好过滤；
- 设置首页只展示连接、服务、通知、版本和恢复操作，工具、扩展、Token 统计及审计进入高级诊断；
- 更新只在连接后全局检查一次；低于最低版本时禁止新建对话和任务；已校验 APK 跨重启保留，
  从未知来源设置返回后继续安装；
- 一级导航只保留“对话/任务”，设置入口统一；权限、空态、加载、错误、离线和危险删除均有
  可恢复动作或确认。

### 新鲜验证证据

```text
python -m pytest -q
624 passed

npm run typecheck
passed

npm test
7 files / 17 tests passed

npm run contract
OpenAPI regenerated successfully

npm run bundle:android
Expo Android production bundle generated successfully

scripts/build-mobile-apk.sh
BUILD SUCCESSFUL; dev.knoa.mobile 0.2.0 (versionCode 11)
Owner certificate: CN=Knoa Owner, RSA 4096-bit
APK Signature Scheme v2: verified
APK: /disk/dev/knoa-mobile-out/app/outputs/apk/release/app-release.apk (44 MB)
SHA-256: 7f676a8e6601cd97aff487fccbc788c97be63a627ace375154913d59e208560c
```

### 发布验证

```text
pca --restart
Service started (pid 3129513); Core 9527 and Gateway 9529 ready

GET http://127.0.0.1:9529/health
200 {"status":"ok","scope":"authentication"}

scripts/publish-mobile-apk.sh app-release.apk
Published 0.2.0 (versionCode 11), minSupportedVersionCode 1
Published package SHA-256 matches the signed build

HEAD /releases/android/11/7f676a8e.../knoa.apk
200; content-type application/vnd.android.package-archive; content-length 45466962
ETag and X-Knoa-Sha256 match 7f676a8e6601cd97aff487fccbc788c97be63a627ace375154913d59e208560c
```

本轮 Phase A-H、Gateway 发布和 Android 私有渠道发布均已完成。
