# Tool Schema 优化设计

> **版本**: 0.1
> **日期**: 2026-08-06
> **状态**: 设计中

---

## 1. 概述

### 1.1 文档范围

优化 PC Assistant 的工具定义体系，使 LLM 在 skim schema 注入时"一眼看懂"每个工具的意图和参数，同时消除当前架构中不必要的间接层。

### 1.2 设计目标

| 目标 | 具体要求 |
|------|----------|
| 一眼看懂 | 工具名即动词短语，参数名即语义，description 点到即止 |
| 本色一套 | 去掉 internal/public 双重命名，代码中只有一套名字 |
| 无全局覆写 | 删除 `LLM_PARAM_DESCRIPTIONS` 全局字典，描述由工具自己声明 |
| skim 带描述 | skim 模式不再丢弃 description，保留 ≤25 字精简描述 |
| 全量注入 | 不做按需加载（避免 cache miss），靠 skim 压缩 token 开销 |

### 1.3 术语定义

| 术语 | 定义 |
|------|------|
| skim schema | 每次 LLM 请求时注入的精简工具描述（当前约占 800–1200 token） |
| full schema | 通过 `tool_help` 按需获取的完整参数文档 |
| action-enum 工具 | 一个工具通过 `action` 参数路由多个子操作（如 files） |

---

## 2. 现状问题

### 2.1 双重命名映射链

```
@parameter("cwd", public_name="working_directory")  ← 声明
     ↓
registry.llm_schema() → aliases 正向映射   ← 注入
     ↓
LLM 输出 "working_directory"
     ↓
registry.normalize_call() → reverse_aliases  ← 反向映射
     ↓
tool.execute(cwd=...)                        ← 执行
```

问题：调试时需在 4 个映射点追踪名称变换。名字不一致增加出错面。

### 2.2 全局字典静默覆写

```python
LLM_PARAM_DESCRIPTIONS = {
    "action": "Operation.",
    "command": "Command.",
    ...
}
```

`llm_schema()` 第 104-106 行：匹配 key 就覆盖，导致工具自己声明的语义描述丢失。

### 2.3 skim 模式丢弃 description

当前 skim 输出：
```json
{"command": {"type": "string"}}
```

LLM 看到裸 type，对于 `content`、`key`、`query` 等泛化参数名完全无法区分语义。

---

## 3. 目标架构

### 3.1 单名制：代码即接口

**决策：代码中的参数名 = LLM 看到的参数名。不再有 alias 层。**

改动原则：
- `execute(**kwargs)` 的参数名直接使用 LLM 友好的名字
- 如果当前内部名不够清晰（如 `cwd`），直接改为 `working_directory`
- 删除 `@parameter(..., public_name=...)` 机制
- 删除 `normalize_call()` 中的反向映射逻辑

### 3.2 工具命名：最自然的动词短语

| 当前 internal | 当前 llm_name | 改为（统一） | 理由 |
|--------------|--------------|-------------|------|
| shell | run_command | run_command | "run_command" 比 "shell" 更明确 |
| filesystem | files | files | 简洁且覆盖读写 |
| application | apps | apps | 已合理 |
| web | web | web | 已合理 |
| system | system_info | system_info | 区分于 shell |
| session | desktop_session | session | "desktop_session" 过长 |
| clipboard | clipboard | clipboard | 已合理 |
| memory | memory | memory | 已合理 |
| weather | weather | weather | 已合理 |
| exchange | currency | currency | "currency" 比 "exchange" 明确 |
| window | windows | windows | 已合理 |
| notification | notifications | notify | 更短且是动词 |
| ui | ui_elements | ui | 已合理 |
| screen | screen | screen | 已合理 |
| keyboard | keyboard | keyboard | 已合理 |
| mouse | mouse | mouse | 已合理 |
| scheduler | scheduler | schedule | 动词形式更自然 |
| screenshot | take_screenshot | screenshot | 统一为名词，调用即触发 |
| artifact_prepare | attach_file | attach | 最简动词 |
| image_inspect | inspect_image | inspect_image | 已合理 |
| describe_tool | tool_help | tool_help | 已合理 |

### 3.3 Skim Schema 格式规范

**核心原则：参数名能说清的不加 description，加了就必须提供增量信息。**

三类参数处理：

| 参数名清晰度 | 例子 | description |
|-------------|------|-------------|
| 名字即语义 | `command`, `url`, `path`, `query`, `text` | 省略或 `null` |
| 名字有歧义 | `key`（是什么 key？）、`content`（什么内容？） | 加一句（≤20字） |
| 有默认值/约束 | `timeout_seconds`, `max_results` | 仅写约束，如 `"default 30"` |

示例：

```json
{
  "name": "run_command",
  "description": "Execute a shell command, return stdout/stderr.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {"type": "string"},
      "timeout_seconds": {"type": "integer", "description": "default 30"},
      "working_directory": {"type": "string"}
    },
    "required": ["command"]
  }
}
```

**规则**：
1. 工具 description 用动词短语，说明做什么 + 产出什么
2. 参数 description 只在名字不够清晰时补充
3. enum 保留完整枚举值（LLM 决策的关键信息）
4. action-enum 工具：仅当 action 含义不直观时加 `key=value` 格式说明

### 3.4 各工具 Skim Schema 设计

#### run_command
```json
{
  "name": "run_command",
  "description": "Execute a shell command, return stdout/stderr.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {"type": "string"},
      "timeout_seconds": {"type": "integer", "description": "default 30"},
      "working_directory": {"type": "string"}
    },
    "required": ["command"]
  }
}
```

#### files
```json
{
  "name": "files",
  "description": "Read, write, list, copy, move, or delete files and folders.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["read", "write", "list", "mkdir", "delete", "copy", "move", "exists"]},
      "path": {"type": "string"},
      "content": {"type": "string", "description": "for write"},
      "destination": {"type": "string", "description": "for copy/move"}
    },
    "required": ["action", "path"]
  }
}
```

#### web
```json
{
  "name": "web",
  "description": "Search the web or fetch a URL as text.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["search", "fetch"]},
      "query": {"type": "string"},
      "url": {"type": "string"},
      "max_results": {"type": "integer", "description": "default 5"}
    },
    "required": ["action"]
  }
}
```

#### keyboard
```json
{
  "name": "keyboard",
  "description": "Press keys, type text, or send shortcuts.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["press", "type", "hotkey", "paste"], "description": "press=one key, type=text, hotkey=combo, paste=via clipboard"},
      "key": {"type": "string", "description": "e.g. enter, tab, f1"},
      "keys": {"type": "array", "items": {"type": "string"}, "description": "e.g. [ctrl, c]"},
      "text": {"type": "string"}
    },
    "required": ["action"]
  }
}
```

#### mouse
```json
{
  "name": "mouse",
  "description": "Move, click, scroll, or drag the pointer.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["position", "move", "click", "double_click", "right_click", "scroll", "drag"]},
      "x": {"type": "integer"},
      "y": {"type": "integer"},
      "x2": {"type": "integer", "description": "drag end X"},
      "y2": {"type": "integer", "description": "drag end Y"},
      "dx": {"type": "integer", "description": "scroll horizontal"},
      "dy": {"type": "integer", "description": "scroll vertical (+up)"},
      "button": {"type": "string", "enum": ["left", "right", "middle"]}
    },
    "required": ["action"]
  }
}
```

#### memory
```json
{
  "name": "memory",
  "description": "Store, retrieve, search, or delete user facts.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["store", "retrieve", "search", "delete"]},
      "key": {"type": "string", "description": "snake_case fact key"},
      "value": {"type": "string", "description": "for store"},
      "category": {"type": "string"},
      "importance": {"type": "string", "enum": ["core", "relevant"]}
    },
    "required": ["action"]
  }
}
```

#### screenshot
```json
{
  "name": "screenshot",
  "description": "Capture full desktop as image.",
  "parameters": {"type": "object", "properties": {}}
}
```

#### inspect_image
```json
{
  "name": "inspect_image",
  "description": "Ask a question about an image by its ID.",
  "parameters": {
    "type": "object",
    "properties": {
      "image_id": {"type": "string"},
      "question": {"type": "string", "description": "what to look for"}
    },
    "required": ["image_id", "question"]
  }
}
```

#### system_info
```json
{
  "name": "system_info",
  "description": "Query OS state: CPU, memory, disk, network, battery.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["overview", "cpu", "memory", "disk", "network", "battery"]}
    },
    "required": ["action"]
  }
}
```

#### apps
```json
{
  "name": "apps",
  "description": "Launch, close, list, or find running applications.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["launch", "close", "list", "find", "info"]},
      "name": {"type": "string"},
      "pid": {"type": "integer", "description": "for close/info"}
    },
    "required": ["action"]
  }
}
```

#### windows
```json
{
  "name": "windows",
  "description": "List, focus, move, resize, or close desktop windows.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["list", "focus", "move", "resize", "minimize", "maximize", "close"]},
      "window_id": {"type": "string"},
      "x": {"type": "integer"},
      "y": {"type": "integer"},
      "width": {"type": "integer"},
      "height": {"type": "integer"}
    },
    "required": ["action"]
  }
}
```

#### clipboard
```json
{
  "name": "clipboard",
  "description": "Read or write the system clipboard.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["read", "write"]},
      "content": {"type": "string", "description": "for write"}
    },
    "required": ["action"]
  }
}
```

#### notify
```json
{
  "name": "notify",
  "description": "Show a desktop notification.",
  "parameters": {
    "type": "object",
    "properties": {
      "title": {"type": "string"},
      "message": {"type": "string"}
    },
    "required": ["title", "message"]
  }
}
```

#### schedule
```json
{
  "name": "schedule",
  "description": "Set a timed reminder or recurring task.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["add", "list", "cancel"]},
      "message": {"type": "string"},
      "delay_seconds": {"type": "integer"},
      "cron": {"type": "string", "description": "for recurring"},
      "task_id": {"type": "string", "description": "for cancel"}
    },
    "required": ["action"]
  }
}
```

#### attach
```json
{
  "name": "attach",
  "description": "Attach an existing file for user delivery.",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {"type": "string"}
    },
    "required": ["path"]
  }
}
```

#### tool_help
```json
{
  "name": "tool_help",
  "description": "Show full schema and examples for a tool.",
  "parameters": {
    "type": "object",
    "properties": {
      "tool_name": {"type": "string"}
    },
    "required": ["tool_name"]
  }
}
```

#### session
```json
{
  "name": "session",
  "description": "Lock, sleep, or logout the computer.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["lock", "sleep", "logout", "status"]}
    },
    "required": ["action"]
  }
}
```

#### currency
```json
{
  "name": "currency",
  "description": "Convert between currencies.",
  "parameters": {
    "type": "object",
    "properties": {
      "amount": {"type": "number"},
      "from": {"type": "string", "description": "e.g. USD"},
      "to": {"type": "string", "description": "e.g. CNY"}
    },
    "required": ["amount", "from", "to"]
  }
}
```

#### weather
```json
{
  "name": "weather",
  "description": "Get weather for a location.",
  "parameters": {
    "type": "object",
    "properties": {
      "location": {"type": "string"},
      "forecast": {"type": "boolean", "description": "true=multi-day"}
    },
    "required": ["location"]
  }
}
```

#### ui
```json
{
  "name": "ui",
  "description": "Find and interact with UI elements on screen.",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["find", "click", "type", "list"]},
      "element": {"type": "string", "description": "element name or label"},
      "app": {"type": "string", "description": "target application"},
      "text": {"type": "string", "description": "for type action"}
    },
    "required": ["action"]
  }
}
```

### 3.5 Token 预估

按上述 21 个工具的 skim schema（JSON 格式），总计约 **2200 字符 / ~700 token**。比当前版本更短（减少了冗余 description），但每个参数的语义更明确。

### 3.6 System Prompt 并行调用优化

当前 `system.md` 第 5 条：
```
5. Call only one tool at a time. Wait for the result before deciding the next step.
```

这是对**支持 parallel tool call 的模型**的不必要限制，导致独立操作（如同时查天气和汇率）串行化，延迟翻倍。

**改为**：

```markdown
<instructions>
1. Answer directly when you already know the information.
2. Only call tools when you need external information or need to perform an action.
3. Do NOT call the same tool with the same arguments more than once.
4. Give your final answer as soon as you have enough information.
5. When multiple tool calls are independent (results don't depend on each other),
   call them in parallel. When one call needs another's result, call sequentially.
6. If a tool returns an error, try a different approach instead of repeating.
7. Always reply in the same language as the user's input.
8. If a task needs parameters not shown in the tool schema, call tool_help first.
9. When the user denies an operation ([REJECTED:confirmation_denied]),
   do NOT retry or attempt an equivalent operation.
10. Use screenshot when user asks to show/send a screen capture.
    Use attach when user asks to send an existing file.
</instructions>
```

**关键变化**：
- 第 5 条从"只调一个"改为"独立的并行，依赖的串行"
- 精简了部分冗长表述
- 删除了 `<think>` tag 指令（让模型自行决定是否需要内部推理）

**代码影响**：
- `LLMProvider.chat_stream()` 已支持 `tool_calls` 数组返回多个并行调用
- `Agent._run_loop` 需改为支持批量执行：收到多个 tool_call 时并行 `asyncio.gather` 执行
- Verifier 逐个验证，但执行可并行（验证是前置 gate，不需要串行等结果）

---

## 4. 代码改动

### 4.1 删除映射层（`base.py`）

| 删除 | 说明 |
|------|------|
| `ToolParameter.public_name` | 不再需要 |
| `@parameter(..., public_name=...)` | 直接用参数名 |
| `ToolBase.llm_name` | 工具 `name` 就是唯一名称 |
| `ToolBase.llm_skim_description` | 只保留一个 `description` |

保留的 `@parameter` 功能：
- `skim: bool` — 标记是否出现在 skim schema 中
- `description: str` — skim schema 中的精简描述（≤25字）
- `required: bool | None` — 覆盖默认 required 判断

新的 `ToolBase`：

```python
class ToolBase(ABC):
    name: str = ""
    description: str = ""       # skim 级别描述（一句话）
    is_side_effecting: bool = False
    parameters: tuple[ToolParameter, ...] = ()

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any: ...

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """Full JSON schema (for tool_help)."""

    def skim_schema(self) -> dict[str, Any]:
        """Compact schema for injection. Default = full schema."""
        return self.schema()
```

### 4.2 简化 Registry（`registry.py`）

删除：
- `LLM_PARAM_DESCRIPTIONS` 全局字典
- `LLM_ACTION_NAMES` 全局字典
- `llm_schema()` 中的 alias/rename/prefix 逻辑
- `normalize_call()` 中的反向映射

保留：
- `resolve_name()` — 仅做 fallback 容错（LLM 可能输出略有不同的拼写）
- `all_schemas()` — 直接调用 `tool.skim_schema()` 包装为 OpenAI function format
- `detailed_schema()` — 直接调用 `tool.schema()` + examples

新的 `all_schemas()`：

```python
def all_schemas(self) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": tool.skim_schema(),
        }
        for tool in self._tools.values()
    ]
```

### 4.3 工具代码改动（以 shell 为例）

改前：
```python
@parameter("env", public_name="environment")
@parameter("cwd", public_name="working_directory")
@parameter("timeout", public_name="timeout_seconds")
@tool(name="run_command", description="...", skim_description="...")
class ShellTool(ToolBase):
    name = "shell"
    ...
    async def execute(self, **kwargs):
        command = kwargs.get("command", "")
        timeout = kwargs.get("timeout")
        cwd = kwargs.get("cwd")
        env = kwargs.get("env")
```

改后：
```python
class ShellTool(ToolBase):
    name = "run_command"
    description = "Execute a shell command, return stdout/stderr."
    is_side_effecting = True

    async def execute(self, **kwargs):
        command = kwargs.get("command", "")
        timeout_seconds = kwargs.get("timeout_seconds")
        working_directory = kwargs.get("working_directory")
        environment = kwargs.get("environment")
        ...

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout_seconds": {"type": "integer", "description": "Kill after N seconds (default 30)"},
                    "working_directory": {"type": "string", "description": "Run in this directory"},
                    "environment": {"type": "object", "description": "Extra env vars for this command"},
                },
                "required": ["command"],
            },
        }

    def skim_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout_seconds": {"type": "integer", "description": "Kill after N seconds (default 30)"},
                    "working_directory": {"type": "string", "description": "Run in this directory"},
                },
                "required": ["command"],
            },
        }
```

**关键变化**：
- `name = "run_command"` — 代码和 LLM 看到的名字一样
- `execute()` 的参数直接用 LLM 看到的名字
- 不需要任何装饰器做映射
- `skim_schema()` 省略不常用参数（如 `environment`），但保留了 description

---

## 5. Description 写作规范

### 5.1 工具 description（≤50字）

- 以动词开头
- 说明做什么 + 返回什么（如适用）
- 不要说"this tool"或废话

好：`"Execute a shell command, return stdout/stderr."`
坏：`"This tool allows you to run shell commands on the system."`

### 5.2 参数 description：不加 > 加 > 怎么加

**不加**（参数名已经足够清晰）：
- `command`, `url`, `path`, `query`, `text`, `title`, `message`, `name`, `location`
- `x`, `y`, `width`, `height`（坐标/尺寸含义无歧义）
- 所有 `action` 的 enum 值本身就是动词时（如 read/write/list）

**加**（名字有歧义或需要补充约束）：
- `key` → `"snake_case fact key"`（否则不知道是密钥还是标识符）
- `content` → `"for write"`（否则不知道是什么内容）
- `timeout_seconds` → `"default 30"`（补充默认值）
- `forecast` → `"true=multi-day"`（布尔值含义不明时）
- `x2`/`y2` → `"drag end X"`（区分于普通坐标）

**怎么加**：
- 只写增量信息，不重复参数名的含义
- 用最短的片段，不写完整句子
- `"for write"` 比 `"Content to write (for write action)"` 好

### 5.3 action enum 描述

大部分 action enum 不需要描述——`["read", "write", "list", "delete"]` 自解释。

只在 action 含义不直观时加说明：
```json
"description": "press=one key, type=text, hotkey=combo, paste=via clipboard"
```

---

## 6. 迁移策略

### 6.1 分步执行

| 步骤 | 内容 | 影响 |
|------|------|------|
| 1 | 修改 `ToolBase` 和 `ToolRegistry`，删除映射层 | 框架层 |
| 2 | 逐个工具改名 + 重写 skim_schema/schema | 每个工具一个 commit |
| 3 | 更新 `Verifier._validate_arguments` 使用新 schema 路径 | harness 层 |
| 4 | 更新 system prompt（并行调用 + 精简指令） | prompt 层 |
| 5 | Agent._run_loop 支持并行 tool call 批量执行 | agent 核心 |
| 6 | 更新测试 | 验证层 |

### 6.2 兼容性

- 对外协议（WebSocket events）中的 `tool_name` 字段直接使用新名，无需兼容旧名
- `Verifier` 的 schema validation 直接用 `tool.schema()` 的 properties
- 历史 audit log 中的旧名称保持只读不转换

---

## 7. 工具拆分：消除 action-enum 复杂度

### 7.1 问题：action routing = 双层决策

当前 `files(action="read", path=...)` 要求 LLM：
1. 选工具 → `files`
2. 选 action → `read`（从 8 个 enum 中）
3. 选参数 → `read` 只要 `path`，但 `copy` 要 `path`+`destination`

**每个 action 的必选参数不同 = LLM 需要条件推理 → 容易出错。**

### 7.2 拆分原则

| 情况 | 处理 |
|------|------|
| 各 action 参数相同（如 mouse 都用 x/y） | 保持 action-enum |
| 各 action 参数不同（如 files: read 和 copy） | 拆成单独工具 |
| action 数 ≤ 3 且参数相似 | 可保持 |
| shell 能直接做的操作 | 删除，不重复提供 |

### 7.3 拆分后工具清单

**单一职责工具（一个动词，1-3 个参数）**：

| 工具名 | 参数 | 说明 |
|--------|------|------|
| `run_command` | command, timeout_seconds?, working_directory? | 保持 |
| `read_file` | path, offset?, limit? | 支持大文件按行切片 |
| `write_file` | path, content | 创建或覆盖 |
| `web_search` | query, max_results? | 从 web 拆出 |
| `web_fetch` | url | 从 web 拆出 |
| `screenshot` | (无参数) | 保持 |
| `inspect_image` | image_id, question | 保持 |
| `notify` | title, message | 保持 |
| `press_key` | key | 从 keyboard 拆出 |
| `type_text` | text | 从 keyboard 拆出 |
| `hotkey` | keys | 从 keyboard 拆出 |
| `weather` | location, forecast? | 保持 |
| `currency` | amount, from, to | 保持 |

**保持 action-enum 的工具（参数共享）**：

| 工具名 | action enum | 共享参数 |
|--------|-------------|----------|
| `mouse` | position/move/click/scroll/drag | x, y, button |
| `memory` | store/retrieve/search/delete | key, value |
| `windows` | list/focus/move/resize/close | window_id |

**删除的工具（shell 能做，或认知负担过高）**：

| 删除 | 用 shell 替代 |
|------|--------------|
| `system_info` | `uname`, `free -m`, `df -h` |
| `apps` | `open xxx` / `ps aux` / `kill` |
| `session` | `loginctl lock-session` |
| files 的 list/copy/move/delete/mkdir | `ls`, `cp`, `mv`, `rm`, `mkdir` |

### 7.4 拆分后 skim schema 示例

```json
{"name":"read_file","description":"Read a file.","parameters":{"type":"object","properties":{"path":{"type":"string"},"offset":{"type":"integer","description":"start line (1-based)"},"limit":{"type":"integer","description":"max lines"}},"required":["path"]}}
{"name":"write_file","description":"Create or overwrite a file.","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}}
{"name":"web_search","description":"Search the web.","parameters":{"type":"object","properties":{"query":{"type":"string"},"max_results":{"type":"integer","description":"default 5"}},"required":["query"]}}
{"name":"web_fetch","description":"Fetch URL as text.","parameters":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}}
{"name":"press_key","description":"Press a key.","parameters":{"type":"object","properties":{"key":{"type":"string","description":"e.g. enter, tab, f1"}},"required":["key"]}}
{"name":"type_text","description":"Type text.","parameters":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}
{"name":"hotkey","description":"Key combination.","parameters":{"type":"object","properties":{"keys":{"type":"array","items":{"type":"string"},"description":"e.g. [ctrl, c]"}},"required":["keys"]}}
```

**一眼看懂**：每个工具就是一个动词 + 显而易见的参数。无需翻文档。

### 7.5 Token 对比

| 方案 | 工具数 | Skim Token | 认知复杂度 |
|------|--------|------------|-----------|
| 当前（action-enum） | 21 | ~700 | 高（双层决策） |
| 拆分后（单一职责） | ~16 | ~500 | 低（一层选择） |

---

## 8. 飞书卡片流式更新方案

> **状态：✅ 已实现** — commit `faedc4c`

### 8.1 问题

当前飞书端行为：等 agent 全部完成后一次性发送结果。用户看到"卡死→突然出现一大段"。

### 8.2 目标

用户看到一张**实时追加的卡片**：
```
┌─────────────────────────────────┐
│ 💭 Let me check your files...   │  ← thinking（note 区块）
│                                  │
│ ⚙️ read_file("/etc/hosts")       │  ← tool_call
│ ✓ 2 lines                       │  ← tool_result（精简）
│                                  │
│ ⚙️ run_command("grep ...")       │  ← tool_call
│ ✓ 找到 3 条匹配                 │  ← tool_result
│ ─────────────────────────────── │
│ 你的 hosts 文件包含以下条目：...  │  ← final answer
└─────────────────────────────────┘
```

### 8.3 技术方案

飞书 Interactive Card 支持 **PATCH 更新已发送卡片**（`im.v1.message.patch`）。

事件映射（已实现）：
```
首个 tool_call    → POST 创建卡片，保存 message_id
stream_think_delta → 累积，debounce 500ms 后 PATCH（note 区块）
tool_call          → 立即 PATCH，追加 "⚙️ tool_name(args)"
tool_result        → 立即 PATCH，追加 "✓ 精简结果"
final_answer       → 标记 done，loop 结束后最终 PATCH
error/cancelled    → 标记 error，红色卡片
```

### 8.4 实现要点

1. **Debounce**：`_STREAM_DEBOUNCE_SECS = 0.5`（飞书 QPS 限制 ~5/s）
2. **tool_call/tool_result**：`force=True` 立即 PATCH
3. **Thinking**：`note` 元素，截断 300 字符
4. **结果精简**：`_summarize_tool_result()` 只显示行数/成功/错误
5. **卡片状态机**：turquoise（处理中）→ blue（完成）→ red（出错）
6. **Steps 上限**：最多显示最近 8 步，更早的折叠

### 8.5 关键实现

```python
# 核心类：_StreamingCardState（纯数据/渲染，无 I/O）
#   - append_thinking(text) / add_tool_call(name, args) / add_tool_result(summary)
#   - set_answer(text) / set_error(text)
#   - build_card() → dict  （根据当前 phase 渲染完整卡片 JSON）

# FeishuChannel 新增方法：
#   - _send_card_returning_id(open_id, card) → str | None  （POST + 返回 message_id）
#   - _update_card(message_id, card) → bool               （PATCH 已有卡片）

# 事件循环核心模式：
card_state = _StreamingCardState()
message_id = None

async for event in self._agent.run(...):
    if event.type == "tool_call":
        card_state.add_tool_call(...)
        _do_patch(force=True)  # 创建或更新
    elif event.type == "tool_result":
        card_state.add_tool_result(_summarize_tool_result(event.tool_result))
        _do_patch(force=True)
    elif event.type == "stream_think_delta":
        card_state.append_thinking(event.content)
        if _should_patch():  # debounce
            _do_patch()
    ...

_do_patch(force=True)  # 最终完整卡片
```

---

## 附录

### A. 设计决策

#### 决策：去掉按需加载，全量 skim 注入

**背景**：21 个工具的 skim schema 约 900 token，占 8K 窗口的 ~11%。
**选项**：A) 全量注入  B) 按需加载（selector 工具）
**决定**：选 A。按需加载引入 cache miss（每次选工具需要一轮 LLM），且 900 token 开销可接受。
**后果**：skim 质量必须足够高，使 LLM 在 900 token 内能准确选工具。

#### 决策：单名制取代双名制

**背景**：当前有 internal name（代码用）和 llm_name（模型用）两套。
**选项**：A) 保持双名 + 映射  B) 代码直接使用 LLM 友好名
**决定**：选 B。映射层增加了 ~50 行运行时逻辑和所有工具的认知开销，收益为零。
**后果**：部分内部变量需重命名（如 `cwd` → `working_directory`），一次性成本。

#### 决策：skim schema 保留精简 description

**背景**：当前 skim 丢弃 description 只保留 type，LLM 对泛化参数名无法区分。
**选项**：A) 裸 type  B) type + ≤25字 description  C) 仅歧义参数加描述
**决定**：选 C。参数名自解释的不加（如 `command`、`url`），只有歧义参数加极短提示。
**后果**：token 开销甚至低于当前版本，同时消除了关键歧义。

#### 决策：支持并行 tool call

**背景**：system prompt 强制 "Call only one tool at a time"，独立操作被串行化。
**选项**：A) 保持串行  B) 允许并行
**决定**：选 B。现代 LLM（OpenAI/Anthropic）原生支持 parallel tool calls，独立操作并行可减少 50% 延迟。
**后果**：Agent 循环需改为 `asyncio.gather` 批量执行；Verifier 仍逐个校验（安全不可并行跳过）。

### B. 修订历史

| 版本 | 日期 | 修订人 | 修订描述 |
|-----|------|-------|---------|
| 0.1 | 2026-08-06 | Agent | 初始版本 |
