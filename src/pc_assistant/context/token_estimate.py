"""Per-model-family token estimation with runtime calibration.

The estimator uses per-family character→token ratios (CJK chars are denser than
ASCII) and supports incremental calibration from observed usage reports so the
ratios converge towards the real tokenizer behavior of the active model.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

# Per-family default ratios. CJK (and other wide scripts) count ~1-1.5 tokens
# per character; ASCII counts roughly 4 chars per token.
_DEFAULTS: dict[str, dict[str, float]] = {
    "llamacpp": {"cjk_per_token": 1.0, "ascii_chars_per_token": 4.0},
    "openai": {"cjk_per_token": 1.5, "ascii_chars_per_token": 4.0},
    "anthropic": {"cjk_per_token": 1.5, "ascii_chars_per_token": 4.0},
    "gemini": {"cjk_per_token": 1.2, "ascii_chars_per_token": 4.0},
    "qwen": {"cjk_per_token": 1.2, "ascii_chars_per_token": 4.0},
    "deepseek": {"cjk_per_token": 1.2, "ascii_chars_per_token": 4.0},
    "default": {"cjk_per_token": 1.2, "ascii_chars_per_token": 4.0},
}

_CJK_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]"
)

_UNKNOWN_FAMILIES = {"", "default", "local", "custom"}


def normalize_family(family: str, model_name: str = "") -> str:
    """Resolve a provider/model string to a known tokenizer family."""
    key = (family or "").strip().lower()
    if not key or key in _UNKNOWN_FAMILIES:
        if model_name:
            lowered = model_name.lower()
            if "qwen" in lowered:
                return "qwen"
            if "deepseek" in lowered:
                return "deepseek"
            if "claude" in lowered or "anthropic" in lowered:
                return "anthropic"
            if "gemini" in lowered:
                return "gemini"
            if "gpt" in lowered or "o1" in lowered or "o3" in lowered:
                return "openai"
            if "llama" in lowered or "q3" in lowered or "gguf" in lowered:
                return "llamacpp"
        return "default"
    if key in _DEFAULTS:
        return key
    if "openai" in key:
        return "openai"
    if "anthropic" in key or "claude" in key:
        return "anthropic"
    if "gemini" in key:
        return "gemini"
    if "qwen" in key:
        return "qwen"
    if "deepseek" in key:
        return "deepseek"
    if "llama" in key:
        return "llamacpp"
    return "default"


def count_cjk(text: str) -> int:
    return len(_CJK_RE.findall(text))


def estimate_text_tokens(text: str, family: str = "default") -> int:
    """Estimate tokens for a single string under the given family."""
    if not text:
        return 0
    norm = normalize_family(family)
    ratios = _DEFAULTS.get(norm, _DEFAULTS["default"])
    cjk = count_cjk(text)
    other = len(text) - cjk
    return max(0, round(cjk * ratios["cjk_per_token"] + other / ratios["ascii_chars_per_token"]))


class TokenEstimator:
    """Thread-safe estimator with runtime calibration support."""

    def __init__(self, family: str = "default") -> None:
        self.family = normalize_family(family)
        self._ratios = dict(_DEFAULTS.get(self.family, _DEFAULTS["default"]))
        self._lock = threading.Lock()
        self._samples = 0

    @property
    def effective_family(self) -> str:
        return self.family

    def text_tokens(self, text: str) -> int:
        if not text:
            return 0
        with self._lock:
            ratios = dict(self._ratios)
        cjk = count_cjk(text)
        other = len(text) - cjk
        return max(0, round(cjk * ratios["cjk_per_token"] + other / ratios["ascii_chars_per_token"]))

    def messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        total = 0
        for m in messages:
            for field in ("content", "reasoning_content"):
                content = m.get(field) or ""
                if isinstance(content, list):
                    text = "".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    text = str(content)
                total += self.text_tokens(text)
            tcs = m.get("tool_calls") or m.get("delta_tool_calls")
            if tcs:
                total += self.text_tokens(json.dumps(tcs, ensure_ascii=False))
        return total

    def calibrate(self, observed_tokens: int, text: str) -> None:
        """Nudge ratios towards an observed (chars, tokens) sample."""
        if observed_tokens <= 0 or not text:
            return
        with self._lock:
            cjk = count_cjk(text)
            other = len(text) - cjk
            if other > 0:
                observed_per_token = other / max(1, observed_tokens)
                self._ratios["ascii_chars_per_token"] = 0.9 * self._ratios["ascii_chars_per_token"] + 0.1 * observed_per_token
            self._samples += 1

    def sample_count(self) -> int:
        return self._samples

    def ratios(self) -> dict[str, float]:
        with self._lock:
            return dict(self._ratios)


def estimate_messages_tokens(
    messages: list[dict[str, Any]],
    family: str = "default",
    estimator: TokenEstimator | None = None,
) -> int:
    if estimator is not None:
        return estimator.messages_tokens(messages)
    return TokenEstimator(family).messages_tokens(messages)
