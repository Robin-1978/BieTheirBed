# Knoa 架构优化执行计划

基于 `full-architecture-review.md` 评审结果，按 Phase 1→2→3 顺序执行。

---

## Phase 1: 安全加固（P0 修复，5步）

### Step 1: ShellTool 默认超时
- 文件：`src/pc_assistant/tools/shell.py`
- 修改：`execute()` 中 `timeout` 参数默认值从 `None` 改为读取 `AppConfig.shell_timeout`（30s）
- 需要在 `__init__` 中接收 config 或直接硬编码默认 30s

### Step 2: FilesystemTool 文件大小限制
- 文件：`src/pc_assistant/tools/filesystem.py`
- 修改：`_read` 限制最大读取 1MB，超过则截断并提示；`_write` 限制最大写入 1MB

### Step 3: WebTool SSRF 防护
- 文件：`src/pc_assistant/tools/web.py`
- 修改：`_fetch` 中验证 URL scheme 仅允许 http/https；解析目标 IP 禁止 127.0.0.1/10.x/172.16-31.x/192.168.x

### Step 4: DDG 搜索异步化
- 文件：`src/pc_assistant/tools/web.py`
- 修改：`_search_ddg` 中用 `asyncio.to_thread()` 包装同步 `DDGS().text()` 调用

### Step 5: 安全检查集成到 ToolRegistry
- 文件：`src/pc_assistant/tools/registry.py`、`src/pc_assistant/harness/safety.py`
- 修改：`registry.execute()` 中调用 `safety.check_tool_call()` 作为强制前置检查

---

## Phase 2: 上下文管理优化（P1 修复，7步）

### Step 6: 移除 ConversationManager 消息数量截断
- 文件：`src/pc_assistant/context/conversation.py`
- 修改：删除 `add()` 中的 `if len(self._messages) > self._max_messages` 截断逻辑

### Step 7: truncator 摘要角色修正
- 文件：`src/pc_assistant/context/truncator.py`
- 修改：摘要消息角色从 `"user"` 改为 `"system"`

### Step 8: 删除死代码 summarize_old_messages
- 文件：`src/pc_assistant/context/conversation.py`
- 修改：删除 `summarize_old_messages()` 方法

### Step 9: 修复 Token 估算
- 文件：`src/pc_assistant/context/truncator.py`
- 修改：补全 CJK 范围（扩展到 CJK Unified Ideographs Extension A/B），空字符串返回 0

### Step 10: System prompt 优化
- 文件：`src/pc_assistant/context/system_prompt.py`
- 修改：添加语言指引 "Always reply in the same language as the user's input"；安全规则添加具体示例

### Step 11: 修复天气工具 forecast 参数
- 文件：`src/pc_assistant/tools/weather.py`
- 修改：forecast 模式使用 `?format=j1` + 只返回 3 天预报摘要；current 模式只返回当前天气

### Step 12: 修复状态栏 status_colors 重复 key
- 文件：`src/pc_assistant/ui/chat.py`
- 修改：修正 `status_colors` 字典，移除重复的 `ready` key，添加 `executing` 对应黄色

---

## Phase 3: 性能与架构优化（P2 修复，7步）

### Step 13: LLMProvider 复用 HTTP 客户端
- 文件：`src/pc_assistant/llm_provider.py`
- 修改：将 `httpx.AsyncClient` 作为实例属性，`__init__` 中创建，提供 `close()` 方法清理

### Step 14: 提取公共 HTTP 客户端工厂
- 新建：`src/pc_assistant/tools/http_client.py`
- 修改：weather.py、exchange.py 使用共享的 `get_http_client()` 获取客户端实例

### Step 15: memory.store() 延迟写入
- 文件：`src/pc_assistant/context/memory.py`
- 修改：`store()` 标记 `_dirty=True`，新增 `_flush()` 方法，在 `__del__` 或定期调用时写磁盘

### Step 16: 工具偏好动态生成
- 文件：`src/pc_assistant/tools/base.py`、`src/pc_assistant/context/system_prompt.py`
- 修改：ToolBase 新增 `preferred_for: list[str] = []` 类属性；system_prompt 从注册表自动生成偏好规则

### Step 17: 汇率工具代码去重
- 文件：`src/pc_assistant/tools/exchange.py`
- 修改：提取 `_fetch_rate()` 公共方法，`_get_rate` 和 `_convert` 调用它

### Step 18: httpx 导入移到模块顶部
- 文件：`src/pc_assistant/tools/weather.py`、`src/pc_assistant/tools/exchange.py`
- 修改：将 `import httpx` 从方法内部移到模块顶部

### Step 19: reset_conversation() 重置记忆上下文
- 文件：`src/pc_assistant/agent.py`
- 修改：`reset_conversation()` 中重新注入记忆上下文到系统提示词

---

## 执行后验证

- 运行 `python -m pytest` 确保全部测试通过
- 提交并推送到 GitHub
