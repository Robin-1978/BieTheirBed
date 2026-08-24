"""Optional semantic recall for deferred MCP tools.

The selector is an Agent-private optimization.  It never changes the authorized
MCP inventory and fails closed to the deterministic lexical selector when the
optional local embedding runtime is unavailable.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


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
            logger.info("BGE tool selector ready model=%s", self._model_name)
        except (
            ImportError,
            ModuleNotFoundError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            with self._lock:
                self._unavailable = True
            logger.warning(
                "BGE tool selector unavailable model=%s error=%s",
                self._model_name,
                type(exc).__name__,
            )
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
                self._embeddings = np.asarray(list(model.embed(corpus)))
                self._corpus_key = candidates
            query_embedding = np.asarray(
                list(model.query_embed(query[:2000]))[0]
            )
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
        os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
        from fastembed import TextEmbedding

        return TextEmbedding(
            model_name=self._model_name,
            cache_dir=str(_model_cache()),
            providers=["CPUExecutionProvider"],
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
            _DEFAULT_SELECTOR.start_loading()
        return _DEFAULT_SELECTOR


def verify_semantic_runtime(*, provision: bool = False) -> dict[str, Any]:
    """Load BGE and execute a bounded inference probe used by installers/CI."""

    import numpy as np

    os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
    from fastembed import TextEmbedding

    model_name = os.environ.get("KNOA_BGE_MODEL", "BAAI/bge-small-zh-v1.5")
    try:
        model = TextEmbedding(
            model_name=model_name,
            cache_dir=str(_model_cache()),
            providers=["CPUExecutionProvider"],
            local_files_only=True,
        )
    except (OSError, RuntimeError, ValueError):
        if not provision:
            raise
        model = TextEmbedding(
            model_name=model_name,
            cache_dir=str(_model_cache()),
            providers=["CPUExecutionProvider"],
        )
    documents = (
        "browser navigate. Navigate to an explicit safe web URL. "
        "打开浏览器并访问安全的网页网址。",
        "jira issue search. Search and inspect Jira work items.",
    )
    query = np.asarray(list(model.query_embed("打开浏览器并访问网页"))[0])
    browser = np.asarray(list(model.embed([documents[0]]))[0])
    score = float(np.dot(query, browser))
    if not math.isfinite(score) or score < 0.55:
        raise RuntimeError("BGE inference smoke test did not recall the Browser tool")
    return {
        "status": "ready",
        "model": model_name,
        "dimensions": int(len(query)),
        "browser_similarity": round(score, 6),
    }


def _model_cache() -> Path:
    configured = os.environ.get("KNOA_BGE_CACHE", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else Path.home() / ".cache" / "knoa" / "fastembed"
    )
