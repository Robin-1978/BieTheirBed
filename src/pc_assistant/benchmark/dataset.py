from __future__ import annotations

import json
from pathlib import Path

from pc_assistant.benchmark.types import BenchmarkQuestion


def load_dataset(path: str | Path) -> list[BenchmarkQuestion]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    questions: list[BenchmarkQuestion] = []
    with open(path, encoding="utf-8") as f:
        for line_num, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                data = json.loads(line)
                q = BenchmarkQuestion.model_validate(data)
                questions.append(q)
            except Exception as e:
                raise ValueError(
                    f"Invalid benchmark question at {path}:{line_num}: {e}"
                ) from e

    if not questions:
        raise ValueError(f"No valid questions found in dataset: {path}")

    return questions


def load_datasets_from_dir(directory: str | Path, categories: list[str] | None = None) -> list[BenchmarkQuestion]:
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    all_questions: list[BenchmarkQuestion] = []
    for jsonl_path in sorted(directory.glob("*.jsonl")):
        questions = load_dataset(jsonl_path)
        if categories:
            questions = [q for q in questions if q.category in categories]
        all_questions.extend(questions)

    if not all_questions:
        cat_info = f" (categories: {categories})" if categories else ""
        raise ValueError(f"No questions found in {directory}{cat_info}")

    return all_questions