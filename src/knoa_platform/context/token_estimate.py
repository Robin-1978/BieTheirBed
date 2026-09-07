"""Per-model-family token estimation with BPE support and runtime calibration.

Supports native BPE tokenization via tiktoken with seamless fallback to
calibrated character-ratio heuristics. Also provides persistent empirical
calibration from observed usage reports so the ratios converge towards the
real tokenizer behavior of the active model.
"""
from __future__ import annotations

import json
from pathlib import Path
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


class TokenCalibrationStore:
    """Persistent storage for model-specific token calibration factors."""

    def __init__(self, store_path: Path | str | None = None) -> None:
        self.path = Path(store_path) if store_path else None
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        if self.path and self.path.exists():
            self.load()

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._cache = data.get("models", {})
            except Exception:
                pass

    def get_calibration(self, model_key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._cache.get(model_key)

    def update_calibration(
        self,
        model_key: str,
        *,
        observed_tokens: int,
        estimated_tokens: int,
        smoothing: float = 0.15,
    ) -> float:
        """Update calibration factor using EMA and persist to disk."""
        if observed_tokens <= 0 or estimated_tokens <= 0:
            return 1.0
        ratio = max(0.5, min(2.5, observed_tokens / estimated_tokens))
        with self._lock:
            entry = self._cache.get(model_key, {"factor": 1.0, "samples": 0})
            current_factor = float(entry.get("factor", 1.0))
            samples = int(entry.get("samples", 0)) + 1
            new_factor = (1.0 - smoothing) * current_factor + smoothing * ratio
            self._cache[model_key] = {
                "factor": round(new_factor, 4),
                "samples": samples,
                "last_ratio": round(ratio, 4),
            }
            res = new_factor
        self._persist()
        return res

    def _persist(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with self._lock:
                payload = {"version": 1, "models": dict(self._cache)}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            tmp.replace(self.path)
        except Exception:
            pass


class TokenEstimator:
    """Thread-safe estimator with BPE support and runtime calibration."""

    _ENCODINGS: dict[str, Any] = {}
    _ENCODINGS_LOCK = threading.Lock()

    def __init__(
        self,
        family: str = "default",
        model_name: str = "",
        store: TokenCalibrationStore | None = None,
        use_bpe: bool = True,
    ) -> None:
        self.model_name = (model_name or "").strip()
        self.family = normalize_family(family, self.model_name)
        self.model_key = self.model_name or self.family
        self._ratios = dict(_DEFAULTS.get(self.family, _DEFAULTS["default"]))
        self._lock = threading.RLock()
        self._samples = 0
        self._calibration_factor = 1.0
        self._store = store
        self._use_bpe = use_bpe
        self._encoding = None

        if self._store:
            cal = self._store.get_calibration(self.model_key)
            if cal:
                self._calibration_factor = float(cal.get("factor", 1.0))
                self._samples = int(cal.get("samples", 0))

        if self._use_bpe:
            self._encoding = self._resolve_bpe_encoding(self.family, self.model_name)

    @classmethod
    def _resolve_bpe_encoding(cls, family: str, model_name: str) -> Any | None:
        """Resolve and cache tiktoken encoding for the target family/model."""
        try:
            import tiktoken
        except ImportError:
            return None

        # OpenAI o1/o3/4o series use o200k_base; others mostly use cl100k_base or close BPE
        lowered = model_name.lower()
        if any(tag in lowered for tag in ("o1", "o3", "4o", "chatgpt-4o")):
            encoding_name = "o200k_base"
        else:
            encoding_name = "cl100k_base"

        with cls._ENCODINGS_LOCK:
            if encoding_name in cls._ENCODINGS:
                return cls._ENCODINGS[encoding_name]
            try:
                enc = tiktoken.get_encoding(encoding_name)
                cls._ENCODINGS[encoding_name] = enc
                return enc
            except Exception:
                return None

    @property
    def effective_family(self) -> str:
        return self.family

    @property
    def calibration_factor(self) -> float:
        with self._lock:
            return self._calibration_factor

    def _heuristic_text_tokens(self, text: str) -> int:
        cjk = count_cjk(text)
        other = len(text) - cjk
        with self._lock:
            ratios = dict(self._ratios)
        return max(
            0,
            round(
                cjk * ratios["cjk_per_token"]
                + other / ratios["ascii_chars_per_token"]
            ),
        )

    def text_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._encoding is not None:
            try:
                base = len(self._encoding.encode_ordinary(text))
            except Exception:
                base = self._heuristic_text_tokens(text)
        else:
            base = self._heuristic_text_tokens(text)
        with self._lock:
            factor = self._calibration_factor
        return max(1, round(base * factor)) if base > 0 else 0

    def messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        total = 0
        # Message wrapping delimiter overhead (~4 tokens per message for <|im_start|>{role}\n{content}<|im_end|>)
        # plus assistant priming (3 tokens).
        if messages:
            total += len(messages) * 4 + 3

        for m in messages:
            for field in ("content", "reasoning_content"):
                content = m.get(field) or ""
                if isinstance(content, list):
                    text = "".join(
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                    total += self.text_tokens(text)
                    total += self._image_tokens(content)
                else:
                    text = str(content)
                    total += self.text_tokens(text)
            tcs = m.get("tool_calls") or m.get("delta_tool_calls")
            if tcs:
                total += self.text_tokens(json.dumps(tcs, ensure_ascii=False))
        return total

    @staticmethod
    def _image_tokens(content: list[dict[str, Any]]) -> int:
        """Token cost of image blocks within a block-list message."""
        from knoa_platform.vision.preprocess import estimate_image_tokens

        total = 0
        for b in content:
            if not isinstance(b, dict) or b.get("type") not in ("image", "image_ref"):
                continue
            width = int(b.get("width", 0) or 0)
            height = int(b.get("height", 0) or 0)
            if width <= 0 or height <= 0:
                total += 170
            else:
                total += estimate_image_tokens(width, height)
        return total

    def calibrate(
        self,
        observed_tokens: int,
        text: str = "",
        *,
        estimated_tokens: int | None = None,
        model_name: str = "",
    ) -> None:
        """Nudge ratios/calibration factor towards an observed sample."""
        if observed_tokens <= 0:
            return

        with self._lock:
            # Update heuristic character ratios for legacy/heuristic support
            if text:
                cjk = count_cjk(text)
                other = len(text) - cjk
                if other > 0:
                    observed_per_token = other / max(1, observed_tokens)
                    self._ratios["ascii_chars_per_token"] = (
                        0.9 * self._ratios["ascii_chars_per_token"]
                        + 0.1 * observed_per_token
                    )

            # Update calibration factor
            target_key = (model_name or self.model_key).strip()
            if estimated_tokens is not None and estimated_tokens > 0:
                ratio = max(0.5, min(2.5, observed_tokens / estimated_tokens))
                self._calibration_factor = (
                    0.85 * self._calibration_factor + 0.15 * ratio
                )
                self._samples += 1
            elif text:
                # Fallback calibration without explicit estimated_tokens
                base = self.text_tokens(text)
                if base > 0:
                    ratio = max(0.5, min(2.5, observed_tokens / base))
                    self._calibration_factor = (
                        0.85 * self._calibration_factor + 0.15 * ratio
                    )
                self._samples += 1

        if self._store and estimated_tokens and estimated_tokens > 0:
            self._store.update_calibration(
                target_key,
                observed_tokens=observed_tokens,
                estimated_tokens=estimated_tokens,
            )

    def sample_count(self) -> int:
        with self._lock:
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
