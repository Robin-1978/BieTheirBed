from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pc_assistant.agent import Agent
from pc_assistant.benchmark.dataset import load_dataset, load_datasets_from_dir
from pc_assistant.benchmark.evaluator import LLMJudge
from pc_assistant.benchmark.scorer import Scorer
from pc_assistant.benchmark.types import BenchmarkQuestion, BenchmarkResult
from pc_assistant.config import AppConfig
from pc_assistant.llm_provider import LLMProvider


class BenchmarkRunner:
    def __init__(
        self,
        config: AppConfig,
        output_path: str | None = None,
    ):
        self._config = config
        self._output_path = output_path
        self._scorer = Scorer()

    async def run_dataset(self, dataset_path: str) -> list[BenchmarkResult]:
        questions = load_dataset(dataset_path)
        return await self._run_questions(questions)

    async def run_all(
        self, dataset_dir: str, categories: list[str] | None = None
    ) -> list[BenchmarkResult]:
        questions = load_datasets_from_dir(dataset_dir, categories)
        return await self._run_questions(questions)

    async def _run_questions(self, questions: list[BenchmarkQuestion]) -> list[BenchmarkResult]:
        results: list[BenchmarkResult] = []
        judge = self._create_judge()

        for i, q in enumerate(questions):
            print(f"[{i + 1}/{len(questions)}] {q.id}: {q.question[:60]}...", flush=True)
            result = await self._run_question(q, judge)
            results.append(result)
            if self._output_path:
                self._append_result(result)

        return results

    async def _run_question(self, q: BenchmarkQuestion, judge: LLMJudge | None) -> BenchmarkResult:
        if q.setup_command:
            self._run_setup(q.setup_command)

        start_time = time.monotonic()

        agent = self._create_agent(q)
        events: list[Any] = []
        answer: str | None = None
        error_msg: str | None = None
        tool_count = 0

        try:
            async for event in agent.run(q.question):
                events.append(event)
                if event.type == "tool_call" and not event.blocked:
                    tool_count += 1
                elif event.type == "final_answer":
                    answer = event.content
                elif event.type == "error":
                    error_msg = event.content
                elif event.type == "iteration_limit":
                    error_msg = event.content
                elif event.type == "cancelled":
                    error_msg = event.content
        except Exception as e:
            error_msg = str(e)

        elapsed = time.monotonic() - start_time
        status = await agent.get_status()
        actual_tools = list(dict.fromkeys(
            e.tool_name for e in events if e.type == "tool_call" and not e.blocked
        ))
        actual_args = [e.tool_args for e in events if e.type == "tool_call" and not e.blocked]
        blocked = any(e.blocked for e in events if e.type == "tool_call")

        score = 0.0
        eval_detail = ""
        if error_msg:
            eval_detail = f"Error: {error_msg}"
        else:
            answer_str = answer or ""
            if q.eval_method.lower() == "llm_judge" and judge:
                score = await judge.judge(q.question, answer_str, q.eval_rubric)
                eval_detail = f"LLM Judge score: {score}"
            else:
                score = self._scorer.score(q, events, answer_str)
                eval_detail = f"Method: {q.eval_method}, score: {score}"

        if q.teardown_command:
            self._run_setup(q.teardown_command)

        return BenchmarkResult(
            question_id=q.id,
            category=q.category,
            subcategory=q.subcategory,
            difficulty=q.difficulty,
            question=q.question,
            answer=answer,
            score=score,
            weight=q.weight,
            weighted_score=round(score * q.weight, 4),
            eval_method=q.eval_method,
            eval_detail=eval_detail,
            error=error_msg,
            metrics={
                "elapsed_seconds": round(elapsed, 3),
                "prompt_tokens": status["total_prompt_tokens"],
                "completion_tokens": status["total_completion_tokens"],
                "total_tokens": status["total_tokens"],
                "iterations": status["total_iterations"],
                "tool_calls": tool_count,
            },
            actual_tools=actual_tools,
            tool_args=actual_args,
            blocked=blocked,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _create_agent(self, q: BenchmarkQuestion) -> Agent:
        config = self._config.model_copy()
        if q.max_iterations is not None:
            config.max_iterations = q.max_iterations
        return Agent(config=config, disable_tools=q.no_tools)

    def _create_judge(self) -> LLMJudge | None:
        try:
            model = self._config.resolve_model()
            provider = LLMProvider(
                server_url=model.server_url,
                model_name=model.model,
                provider=model.driver,
                api_key=model.api_key,
                api_base=model.api_base,
                timeout=model.timeout,
                thinking=(model.thinking.model_dump() if model.thinking is not None else None),
            )
            return LLMJudge(provider)
        except Exception:
            return None

    @staticmethod
    def _run_setup(command: str) -> None:
        import subprocess
        try:
            subprocess.run(command, shell=True, timeout=10, capture_output=True)
        except Exception:
            pass

    def _append_result(self, result: BenchmarkResult) -> None:
        if not self._output_path:
            return
        path = Path(self._output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(result.model_dump_json() + "\n")
