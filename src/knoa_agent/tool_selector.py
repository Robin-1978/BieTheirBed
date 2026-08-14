"""Optional semantic recall for deferred MCP tools.

The selector is an Agent-private optimization.  It never changes the authorized
MCP inventory and fails closed to the deterministic lexical selector when the
optional local embedding runtime is unavailable.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SemanticSelection:
    names: frozenset[str] = frozenset()
    mode: str = "unavailable"


class BgeToolSelector:
    """Lazy, local-only BGE selector with no mandatory runtime dependency."""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        top_k: int = 6,
        threshold: float = 0.55,
    ) -> None:
        self._model_name = model_name or os.environ.get(
            "KNOA_BGE_MODEL", "BAAI/bge-small-zh-v1.5"
        )
        self._top_k = top_k
        self._threshold = threshold
        self._model: Any = None
        self._unavailable = False
        self._loading = False
        self._lock = threading.Lock()
        self._corpus_key: tuple[tuple[str, str], ...] = ()
        self._embeddings: Any = None
        if os.environ.get("KNOA_BGE_PRELOAD", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            self.start_loading()

    def start_loading(self) -> None:
        """Warm the optional local model without delaying an Agent turn."""

        with self._lock:
            if self._model is not None or self._loading or self._unavailable:
                return
            self._loading = True
        threading.Thread(
            target=self._background_load,
            name="knoa-bge-tool-selector",
            daemon=True,
        ).start()

    def _background_load(self) -> None:
        try:
            model = self._load_model()
            with self._lock:
                self._model = model
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError):
            with self._lock:
                self._unavailable = True
        finally:
            with self._lock:
                self._loading = False

    def select(
        self,
        query: str,
        candidates: tuple[tuple[str, str], ...],
    ) -> SemanticSelection:
        if (
            not query.strip()
            or not candidates
            or self._unavailable
            or self._model is None
        ):
            return SemanticSelection()
        try:
            model = self._model
            import numpy as np

            if candidates != self._corpus_key:
                corpus = [self._document(name, description) for name, description in candidates]
                self._embeddings = model.encode(
                    corpus,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                self._corpus_key = candidates
            query_embedding = model.encode(
                ["为这个句子生成表示以用于检索相关工具：" + query[:2000]],
                normalize_embeddings=True,
                show_progress_bar=False,
            )[0]
            scores = np.dot(self._embeddings, query_embedding)
            ranked = sorted(
                zip(candidates, scores, strict=True),
                key=lambda item: float(item[1]),
                reverse=True,
            )[: self._top_k]
            names = frozenset(
                name
                for (name, _description), score in ranked
                if float(score) >= self._threshold
            )
            return SemanticSelection(names=names, mode="bge")
        except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError):
            self._unavailable = True
            return SemanticSelection()

    def _load_model(self) -> Any:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(
            self._model_name,
            device="cpu",
            local_files_only=True,
        )

    @staticmethod
    def _document(name: str, description: str) -> str:
        readable_name = name.replace("mcp__", "").replace("__", " ").replace("_", " ")
        return f"{readable_name}. {description}".strip()


class DisabledToolSelector:
    """Explicit no-op selector for runtimes that are never granted tools."""

    def start_loading(self) -> None:
        return None

    def select(
        self,
        query: str,
        candidates: tuple[tuple[str, str], ...],
    ) -> SemanticSelection:
        del query, candidates
        return SemanticSelection()


_DEFAULT_SELECTOR: BgeToolSelector | None = None
_DEFAULT_SELECTOR_LOCK = threading.Lock()


def default_tool_selector() -> BgeToolSelector:
    """Return the process-wide selector so the BGE model is loaded once."""

    global _DEFAULT_SELECTOR
    with _DEFAULT_SELECTOR_LOCK:
        if _DEFAULT_SELECTOR is None:
            _DEFAULT_SELECTOR = BgeToolSelector()
        return _DEFAULT_SELECTOR
