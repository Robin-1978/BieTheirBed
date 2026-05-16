# 修复 Agent 无限循环问题

## 问题现象

用户问"你会什么？"时，Agent 陷入无限循环：
1. 第一次问"你能干什么？"正常回答
2. 第二次问"你会什么？"时，LLM 思考后一直不产出最终回答

## 根因分析

### 根因 1：`max_tokens=1024` 太小，LLM 回复被截断

`config.py` 中 `max_tokens: int = 1024`，`default.yaml` 也是 `max_tokens: 1024`。

当 LLM 生成一个较长的回答（尤其是中文回答 + thinking 内容），1024 tokens 很容易不够用。当 `finish_reason == "length"` 时：

```python
# agent.py:675-683
if finish_reason == "length" and clean_content:
    self._conversation.add_assistant_final(clean_content)
    yield AgentEvent(type="final_answer", content=clean_content, ...)
    return
```

这里虽然返回了，但截断的回答可能不完整。更严重的是，如果 thinking 内容很长但 clean_content 为空（全部内容都在 `<think/>` 标签内），就会走到下面的 `not clean_content and full_content` 分支，触发重试循环。

### 根因 2：thinking-only 响应导致死循环

当 LLM 的回复全部在 `<think/>` 标签内（`full_content` 非空但 `clean_content` 为空），代码走到：

```python
# agent.py:699-711
if not clean_content and full_content:
    empty_response_count += 1
    if empty_response_count > max_empty_retries:
        ...return
    self._conversation.add("user", "Please provide your answer based on your thinking.")
    continue
```

这会注入一条 "Please provide your answer based on your thinking." 的用户消息，然后继续循环。但问题是：

- **对话历史不断增长**：每次循环都添加 assistant 的 thinking 内容 + 新的 user 提示
- **token 预算被耗尽**：`truncate_messages` 会截断，但截断后 LLM 仍然可能只输出 thinking
- **最多重试 2 次**（`max_empty_retries=2`），但 2 次后仍可能无法解决

对于 DeepSeek 等思考模型，thinking 很长而 clean_content 为空是常见情况。这不是"空回复"，而是"思考后还没回答"。

### 根因 3：`_ThinkStreamParser` 和 `_strip_think_tags` 双重处理

流式输出时，`_ThinkStreamParser` 已经把 `<think/>` 标签内的内容分离为 `stream_think_delta` 事件。但流结束后，又调用 `_strip_think_tags(full_content)` 做后处理：

```python
# agent.py:522
clean_content, thinking_content = _strip_think_tags(full_content)
```

`full_content` 是原始累积文本（包含 `<think/>` 标签），`_strip_think_tags` 会把标签内内容剥离出来。但如果 `_ThinkStreamParser` 已经正确解析了标签，`clean_content` 应该就是 `think_parser.clean_content`。然而代码没有使用 `think_parser.clean_content`，而是对 `full_content` 重新解析，这两个结果可能不一致（尤其是标签跨 chunk 边界时）。

### 根因 4：对话历史中 tool 消息残留

`conversation.py` 的 `get_messages_for_llm()` 方法中，tool 角色的消息被原样包含在历史中：

```python
# conversation.py:88-93
elif msg.role == "tool":
    result.append({
        "role": "tool",
        "content": msg.content,
        "tool_call_id": msg.tool_call_id or "",
    })
```

但对应的 assistant 消息中 `tool_calls` 被剥离了：

```python
# conversation.py:83-87
elif msg.role == "assistant":
    d: dict[str, Any] = {"role": "assistant", "content": msg.content}
    result.append(d)
```

这导致 LLM 收到的历史中：assistant 消息没有 `tool_calls`，但后面跟着 `role: "tool"` 的消息。很多 LLM API 要求 tool 消息必须跟在带 `tool_calls` 的 assistant 消息后面，否则会报错或行为异常。这种不一致可能导致 LLM 陷入混乱。

### 根因 5：system prompt 过度鼓励工具调用

当前 system prompt 中有 14 个工具的 schema 全部传给 LLM。对于"你会什么？"这种纯问答，LLM 不需要调用任何工具，但工具的存在可能让 LLM 倾向于先调用工具（如 memory search），然后陷入工具调用循环。

## 修复方案

### 修复 1：增大 `max_tokens` 默认值

```yaml
# default.yaml
max_tokens: 4096
```

1024 对中文回复远远不够。4096 是更合理的默认值。

### 修复 2：正确处理 thinking-only 响应

当 `clean_content` 为空但 `full_content` 非空时，不应视为"空回复"重试，而应：

1. 优先使用 `think_parser.clean_content`（流式解析结果更可靠）
2. 如果 `clean_content` 仍为空，说明 LLM 只思考了没回答，应该用一条明确的提示让 LLM 直接回答，而不是含糊的 "Please provide your answer"
3. 限制 thinking-only 重试为 1 次，超过后直接把 thinking 内容作为回答返回

```python
if not clean_content and full_content:
    clean_content = think_parser.clean_content
    if not clean_content:
        empty_response_count += 1
        if empty_response_count > 1:
            self._conversation.add_assistant_final(full_content)
            yield AgentEvent(type="final_answer", content=full_content, ...)
            return
        self._conversation.add("user", "[System] Please provide a direct answer to the user's question. Do not just think, respond with your answer.")
        continue
```

### 修复 3：统一使用 `think_parser` 的解析结果

删除 `_strip_think_tags(full_content)` 的后处理调用，直接使用 `think_parser.clean_content` 和 `think_parser.think_content`：

```python
# 替换 agent.py:522
# 旧: clean_content, thinking_content = _strip_think_tags(full_content)
# 新:
clean_content = think_parser.clean_content
thinking_content = think_parser.think_content
```

### 修复 4：清理对话历史中的孤立 tool 消息

在 `get_messages_for_llm()` 中，如果 assistant 消息没有 `tool_calls`，则跳过后续的 tool 消息：

```python
def get_messages_for_llm(self) -> list[dict[str, Any]]:
    result = [...system...]
    skip_tool = False
    for msg in self._messages:
        if msg.role == "assistant":
            d = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                d["tool_calls"] = msg.tool_calls
                skip_tool = False
            else:
                skip_tool = True
            result.append(d)
        elif msg.role == "tool":
            if not skip_tool:
                result.append({"role": "tool", "content": msg.content, "tool_call_id": msg.tool_call_id or ""})
        elif msg.role == "user":
            skip_tool = True
            result.append({"role": "user", "content": msg.content})
    return result
```

### 修复 5：增强循环检测

当前 `_check_tool_loop` 只检测"相同工具+相同参数连续5次"。应增加：

1. **总工具调用次数限制**：单次 `run()` 中工具调用总次数不超过 N（如 15 次）
2. **无进展检测**：如果连续 3 次工具调用后 LLM 仍未产出 `clean_content`，强制结束

```python
# agent.py run() 方法开头
total_tool_calls = 0
max_total_tool_calls = 15

# 在工具调用后
total_tool_calls += 1
if total_tool_calls >= max_total_tool_calls:
    yield AgentEvent(type="iteration_limit", ...)
    return
```

## 实现步骤

1. **修改 `config/default.yaml`**：`max_tokens: 1024` → `max_tokens: 4096`
2. **修改 `agent.py`**：
   - 用 `think_parser.clean_content` 替换 `_strip_think_tags(full_content)`
   - 修复 thinking-only 响应处理逻辑
   - 增加总工具调用次数限制
   - 增加无进展检测
3. **修改 `conversation.py`**：修复 `get_messages_for_llm()` 中孤立 tool 消息问题
4. **更新测试**：覆盖修复场景
5. **运行测试验证**
