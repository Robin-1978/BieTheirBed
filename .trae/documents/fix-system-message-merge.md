# 修复 llama.cpp "System message must be at the beginning" 错误

## 根因分析

Qwen 模型的 Jinja chat template 只允许 **一个** system message 在消息列表开头。
当前 `get_messages_for_llm()` 生成了 **两个** system message：
```python
[
    {"role": "system", "content": "You are Knoa..."},  # 第1个
    {"role": "system", "content": "Current date: 2026-05-12..."},  # 第2个 ← 问题！
    {"role": "user", "content": "几点了？"},
]
```

Qwen 的 Jinja 模板遍历消息时，发现第2条也是 system，但不在"第一条"的位置，触发 `raise_exception('System message must be at the beginning')`。

## 修复方案

**将 system prompt + date context 合并为一个 system message**，而不是两个。

修改 `ConversationManager.get_messages_for_llm()`：
```python
def get_messages_for_llm(self) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    # 合并为单个 system message
    system_parts = []
    if self._system_prompt:
        system_parts.append(self._system_prompt)
    date_ctx = self._date_context_provider()
    if date_ctx:
        system_parts.append(date_ctx)
    if system_parts:
        result.append({"role": "system", "content": "\n\n".join(system_parts)})

    # ... rest of messages
```

这样发送给 llama.cpp 的消息就是：
```python
[
    {"role": "system", "content": "You are Knoa...\n\nCurrent date: 2026-05-12..."},  # 单个！
    {"role": "user", "content": "几点了？"},
]
```

## 验证步骤

1. 修改 `ConversationManager.get_messages_for_llm()` 合并 system 消息
2. 更新 `test_get_messages_for_llm` 测试（现在只有一个 system message）
3. 写一个实际的端到端测试脚本，调用 llama.cpp 验证不再 500
4. 运行完整测试套件
5. 推送 GitHub
