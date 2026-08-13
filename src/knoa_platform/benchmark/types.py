from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class BenchmarkQuestion(BaseModel):
    id: str
    category: str
    subcategory: str
    difficulty: Literal["easy", "medium", "hard"] = "easy"
    language: Literal["zh", "en", "mixed"] = "en"
    question: str
    no_tools: bool = False
    expected_answer_contains: list[str] = []
    expected_answer_pattern: str | None = None
    expected_tools: list[str] = []
    expected_tool_args: dict[str, list[str]] = {}
    expected_blocked: bool = False
    expected_blocked_reason: str = ""
    max_tool_calls: int | None = None
    max_iterations: int | None = None
    eval_method: str = "contains_all"
    eval_rubric: str = ""
    weight: float = 1.0
    setup_command: str = ""
    teardown_command: str = ""


class BenchmarkResult(BaseModel):
    question_id: str
    category: str
    subcategory: str
    difficulty: str
    question: str
    answer: str | None = None
    score: float = 0.0
    weight: float = 1.0
    weighted_score: float = 0.0
    eval_method: str = ""
    eval_detail: str = ""
    error: str | None = None
    metrics: dict[str, Any] = {}
    actual_tools: list[str] = []
    tool_args: list[dict[str, Any]] = []
    blocked: bool = False
    timestamp: str = ""