# Knoa 全面架构评审与优化计划

## 一、系统架构总览

```
┌─────────────────────────────────────────────────┐
│                    CLI (chat.py)                  │
│  状态栏 / 工具展示 / Thinking / Markdown渲染      │
└───────────────────────┬─────────────────────────┘
                        │ AgentEvent 流
┌───────────────────────▼─────────────────────────┐
│                  Agent (agent.py)                 │
│  ReAct循环 / 事件生成 / 工具调度 / 安全检查        │
└──┬──────────┬──────────┬──────────┬─────────────┘
   │          │          │          │
┌──▼──┐  ┌───▼──┐  ┌───▼──┐  ┌───▼──────┐
│LLM  │  │Context│  │Tools │  │Harness   │
│Prov │  │Mgr   │  │Reg   │  │Safety    │
│     │  │      │  │      │  │Audit     │
│     │  │      │  │      │  │Limiter   │
└─────┘  └──────┘  └──────┘  └──────────┘
```

---

## 二、关键问题汇总（按严重程度排序）

### 🔴 P0 严重问题（5个）

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 1 | **ShellTool 默认无超时** | shell.py:L16 | 命令可无限运行，占满资源 |
| 2 | **FilesystemTool 无文件大小限制** | filesystem.py:L59 | 大文件读取导致 OOM |
| 3 | **WebTool SSRF 风险** | web.py:L42 | 可访问内网服务 |
| 4 | **DDG 搜索同步调用阻塞事件循环** | web.py:L165 | 整个 UI 冻结 |
| 5 | **安全检查与工具执行分离** | 架构级 | 可被绕过 |

### 🟠 P1 高优先级问题（8个）

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 6 | 每次请求创建新 HTTP 客户端 | llm_provider.py:L247 | 无法复用连接，性能差 |
| 7 | 命令安全检查子串匹配误报率高 | safety.py:L58 | `del` 匹配 `delete_backup` 等 |
| 8 | FilesystemTool 同步 I/O 阻塞事件循环 | filesystem.py:L59 | UI 卡顿 |
| 9 | truncator 摘要消息用 `user` 角色而非 `system` | truncator.py:L98 | LLM 误解为用户输入 |
| 10 | ConversationManager 双重截断机制冲突 | conversation.py:L39 | 信息丢失 |
| 11 | 天气工具 forecast 参数无效 | weather.py:L20-23 | current/forecast 请求 URL 相同 |
| 12 | Token 估算 CJK 范围不完整 | truncator.py:L6-11 | 截断不准确 |
| 13 | System prompt 缺少语言/本地化指引 | system_prompt.py | 中文场景体验差 |

### 🟡 P2 中优先级问题（8个）

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| 14 | 工具偏好硬编码在 system prompt | system_prompt.py:L55-59 | 工具增减需手动改 prompt |
| 15 | memory.store() 每次写磁盘 | memory.py:L110 | I/O 压力 |
| 16 | memory.search() 纯子串匹配 | memory.py:L119 | 无法处理同义词 |
| 17 | 缺少记忆时间衰减和冲突检测 | memory.py | 过时记忆排在前面 |
| 18 | 汇率工具代码重复 | exchange.py | 维护困难 |
| 19 | httpx 在方法内部重复导入 | weather/exchange.py | 代码风格 |
| 20 | 自定义异常类未被使用 | exceptions.py | 错误处理不统一 |
| 21 | reset_conversation() 不重置记忆上下文 | agent.py:L624 | 状态不一致 |

---

## 三、分模块详细评审

### 3.1 Agent 核心架构 (agent.py)

**优点**：
- ReAct 循环设计合理：迭代 → LLM 调用 → 工具执行 → 再迭代
- 循环检测机制（`recent_calls` + call signature）防止无限循环
- 事件流设计完整：`stream_start/delta/end`, `think_start/delta/end`, `tool_call/result`, `final_answer`
- 取消机制（`_cancelled` + signal handler）响应及时

**问题**：
1. **`run()` 方法过长（~250行）**：包含 LLM 调用、流式解析、工具执行、错误处理、循环检测等所有逻辑，应拆分为独立方法
2. **thinking 内容和 content 可能重复**：当模型同时返回 `reasoning_content` 字段和 `content` 中的 `<think/>` 标签时，thinking 会被输出两次
3. **空响应重试策略不够智能**：连续空响应时只是重新发送 `[System] You did not produce any output`，应考虑调整 temperature 或简化 prompt

### 3.2 System Prompt 设计 (system_prompt.py)

**优点**：
- 结构清晰：身份 → 环境 → 工具规则 → 安全 → 记忆 → 输出格式
- 条件拼接避免冗余

**问题**：
1. **缺少语言指引**：没有告诉 LLM "用用户使用的语言回复"
2. **工具偏好硬编码**：weather/exchange/timer 的偏好直接写在 prompt 中，工具增减需手动修改
3. **安全规则过于笼统**："Never execute commands that could harm the system" 定义模糊
4. **缺少多轮对话行为指引**：模糊指令时应该澄清还是猜测？

### 3.3 上下文管理 (conversation.py + truncator.py)

**优点**：
- Token 预算机制比消息数量截断更精确
- 工具调用分组确保 assistant tool_calls 和 tool 结果不被拆散

**问题**：
1. **双重截断机制冲突**：ConversationManager 按消息数截断 + truncator 按 token 截断，前者可能提前丢失信息
2. **摘要角色错误**：被丢弃消息的摘要伪装为 `user` 角色，LLM 会误认为用户说了这些话
3. **Token 估算精度不足**：CJK 范围不完整，空字符串返回 1 而非 0
4. **`summarize_old_messages()` 是死代码**：从未被调用，且实现有缺陷
5. **`tool_call_id` 为 None 时填充空字符串**：可能导致 API 错误

### 3.4 记忆系统 (memory.py + memory_tool.py)

**优点**：
- LLM 驱动的记忆工具（store/retrieve/search/delete）语言无关
- 持久化存储跨会话保留
- 丰富的元数据（confidence, access_count, category）

**问题**：
1. **`extract_from_text()` 从未被调用**：自动提取功能完全失效（但已被 MemoryTool 替代，可删除）
2. **`store()` 每次写磁盘**：短时间多次存储产生大量 I/O
3. **`search()` 纯子串匹配**：无法处理同义词、语义相似
4. **缺少时间衰减**：很久以前的高置信度记忆可能已过时
5. **缺少冲突检测**：用户改变偏好时 key-value 语义矛盾

### 3.5 LLM Provider (llm_provider.py)

**优点**：
- 流式 SSE 解析完整
- 区分了 connect/read/write/pool 超时
- 支持 OpenAI/Anthropic/llamacpp 多后端

**问题**：
1. **每次请求创建新 httpx.AsyncClient**：无法复用 TCP 连接
2. **Anthropic 流式处理缺失**：直接退化为非流式
3. **流式中间 chunk 不传递工具调用增量**：失去流式感知
4. **重试逻辑未应用于流式请求**
5. **自定义异常类未被使用**

### 3.6 工具系统

**共性问题**：
1. **ToolBase 过于单薄**：缺少参数验证、安全元数据、超时控制
2. **httpx 客户端创建模式重复**：weather/exchange/web 各自创建
3. **错误处理不一致**：有的返回 `{"error": ...}`，有的抛异常
4. **同步 I/O 阻塞事件循环**：filesystem, memory_tool 的 handler 是同步的

**ShellTool 严重问题**：
- 默认无超时，命令可无限运行
- 进程 kill 后未等待子进程（Windows 孤儿进程）

**FilesystemTool 严重问题**：
- 无文件大小限制，大文件读取导致 OOM
- `_delete` 使用 `shutil.rmtree` 递归删除无防护
- 路径遍历攻击风险

**WebTool 严重问题**：
- SSRF 风险：未验证 URL scheme 和内网地址
- DDG 搜索同步调用阻塞事件循环

### 3.7 安全架构 (safety.py)

**问题**：
1. **命令检查子串匹配误报率高**：`del` 匹配 `delete_backup`，`format` 匹配 `reformat`
2. **注入检测不全面**：未覆盖 PowerShell `& {}`、`cmd &`、环境变量注入
3. **`check_path` 的 `write` 参数未使用**：受保护路径无论读写都拒绝
4. **未覆盖 application 工具**：launch/kill 无安全检查
5. **安全检查与工具执行分离**：需 Agent 层手动调用，可被绕过

### 3.8 CLI 界面 (chat.py)

**优点**：
- 紧凑单行状态栏（已优化）
- 工具调用单行摘要 + 折叠详情
- Thinking dim italic 流式输出
- AI 回答前分隔线

**问题**：
1. **状态栏 `status_colors` 字典有重复 key**：`ready` 出现两次
2. **工具结果展示无法区分结构化数据和纯文本**：天气返回 dict 时显示不够友好
3. **缺少进度指示**：工具执行时只有 spinner，无进度百分比

---

## 四、优化实施计划

### Phase 1: 安全加固（P0 修复）

| Step | 修改 | 文件 |
|------|------|------|
| 1 | ShellTool 使用 `AppConfig.shell_timeout` 作为默认超时 | shell.py |
| 2 | FilesystemTool 添加文件大小限制（读 1MB/写 1MB） | filesystem.py |
| 3 | WebTool 添加 SSRF 防护（仅允许 http/https，禁止内网 IP） | web.py |
| 4 | DDG 搜索用 `asyncio.to_thread()` 包装 | web.py |
| 5 | 安全检查集成到 ToolRegistry.execute() 作为强制中间件 | registry.py, safety.py |

### Phase 2: 上下文管理优化（P1 修复）

| Step | 修改 | 文件 |
|------|------|------|
| 6 | 移除 ConversationManager 的消息数量截断，统一由 truncator 管理 | conversation.py |
| 7 | truncator 摘要角色从 `user` 改为 `system` | truncator.py |
| 8 | 删除 `summarize_old_messages()` 死代码 | conversation.py |
| 9 | 修复 Token 估算：补全 CJK 范围，空字符串返回 0 | truncator.py |
| 10 | System prompt 添加语言指引和具体安全示例 | system_prompt.py |
| 11 | 修复天气工具 forecast 参数逻辑 | weather.py |
| 12 | 修复状态栏 `status_colors` 重复 key | chat.py |

### Phase 3: 性能与架构优化（P2 修复）

| Step | 修改 | 文件 |
|------|------|------|
| 13 | LLMProvider 复用 httpx.AsyncClient 实例 | llm_provider.py |
| 14 | 提取公共 HTTP 客户端工厂，统一连接池管理 | 新建 http_client.py |
| 15 | memory.store() 实现延迟写入（debounce） | memory.py |
| 16 | 工具偏好从 system prompt 移到工具 schema 的 `preferred_for` 字段 | base.py, system_prompt.py |
| 17 | 汇率工具提取公共 API 调用方法 | exchange.py |
| 18 | httpx 导入移到模块顶部 | weather.py, exchange.py |
| 19 | reset_conversation() 重置记忆上下文 | agent.py |

### Phase 4: Agent 重构（可选）

| Step | 修改 | 文件 |
|------|------|------|
| 20 | 拆分 `run()` 为 `_call_llm()`, `_execute_tools()`, `_handle_response()` 等子方法 | agent.py |
| 21 | 增强 ToolBase：添加 `danger_level`, `requires_confirmation`, `validate()` | base.py |
| 22 | 统一错误处理：使用自定义异常类 | 全局 |
| 23 | FilesystemTool 异步化（aiofiles） | filesystem.py |

---

## 五、预期效果

- **安全性**：Shell 超时、文件大小限制、SSRF 防护、安全检查不可绕过
- **稳定性**：消除事件循环阻塞、双重截断冲突、摘要角色错误
- **性能**：HTTP 连接复用、记忆延迟写入、减少 I/O 开销
- **可维护性**：Agent 方法拆分、工具偏好动态生成、死代码清理
- **用户体验**：语言本地化指引、天气工具修复、状态栏修复
