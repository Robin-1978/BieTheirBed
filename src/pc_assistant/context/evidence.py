"""Evidence policy — decide whether a turn must be grounded in tool output.

Prompt-layer enforcement: when a user request asks for current/stateful/factual
information, we (a) inject an instruction telling the model to back claims with
tool results and mark unverified claims, and (b) emit an `evidence_warning` event
if the final answer was produced without any tool call.
"""
from __future__ import annotations

import re

# Queries that depend on live/stateful data — these require tool evidence.
_REQUIRE_EVIDENCE = re.compile(
    r"(?:\d+(?:[.,]\d+)*%?)"           # numbers / percentages
    r"|多少|几个|价格|行情|涨|跌|股票|基金|汇率|天气|温度|"
    r"当前|最新|现在|时间|进程|运行|占用|列表|文件|目录|"
    r"whois|current|weather|price|quote|latest|time|进程|ps|status",
    re.IGNORECASE,
)

# Pure knowledge / trivia questions that do NOT need tool evidence.
_NO_EVIDENCE = re.compile(
    r"^(\s*)(hi|hello|hey|你好|你是谁|what are you|介绍一下你自己|"
    r"help|帮助|who are you)(\s*[?!。.]?\s*)$",
    re.IGNORECASE,
)

_INSTRUCTION = (
    "## Evidence requirement\n"
    "This request asks for current or factual information. "
    "Base your answer on the tool results you actually received. "
    "Do not invent numbers, prices, or system state from memory. "
    "If you cannot verify a claim with a tool, say so explicitly."
)


class EvidencePolicy:
    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def requires_evidence(self, user_text: str) -> bool:
        if not self._enabled or not user_text:
            return False
        stripped = user_text.strip()
        if _NO_EVIDENCE.match(stripped):
            return False
        return bool(_REQUIRE_EVIDENCE.search(stripped))

    def build_instruction(self) -> str:
        return _INSTRUCTION

    def satisfied(self, evidence_tool_calls: int) -> bool:
        return evidence_tool_calls > 0
