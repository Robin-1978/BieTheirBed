from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from pc_assistant.agent_runtime.composition import build_core_runtime
from pc_assistant.agent_runtime.contracts import RunEvent
from pc_assistant.agent_runtime.http_provider import HttpModelProvider
from pc_assistant.benchmark.dataset import load_dataset, load_datasets_from_dir
from pc_assistant.benchmark.evaluator import LLMJudge
from pc_assistant.benchmark.scorer import Scorer
from pc_assistant.benchmark.types import BenchmarkQuestion, BenchmarkResult
from pc_assistant.config import AppConfig
from pc_assistant.runtime import RuntimePaths
from pc_assistant.service.core_client import CoreClient
from pc_assistant.service.credentials import resolve_local_service_token


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

        config = self._question_config(q)
        events: list[RunEvent] = []
        answer: str | None = None
        error_msg: str | None = None
        tool_count = 0

        try:
            composition = build_core_runtime(config)
            await composition.host.start()
            client: CoreClient | None = None
            try:
                client = await CoreClient.connect(
                    f"ws://{config.service_host}:{composition.host.bound_tcp_port}",
                    resolve_local_service_token(
                        RuntimePaths.from_root(config.runtime_root)
                    ),
                )
                session_handle = await client.create_session()
                answer_parts: list[str] = []
                async for event in client.run(
                    session_handle,
                    q.question,
                    tools_enabled=not q.no_tools,
                ):
                    events.append(event)
                    if event.event_type == "content_delta":
                        answer_parts.append(event.payload.content)
                    elif event.event_type == "final_output":
                        answer_parts[:] = [event.payload.content]
                    elif event.event_type == "tool_call":
                        tool_count += 1
                    elif event.event_type in {"failed", "cancelled"}:
                        error_msg = event.payload.content or event.event_type
                answer = "".join(answer_parts).strip()
            finally:
                if client is not None:
                    await client.disconnect()
                await composition.host.stop()
        except Exception as e:
            error_msg = str(e)

        elapsed = time.monotonic() - start_time
        actual_tools = list(dict.fromkeys(
            event.payload.tool_name
            for event in events
            if event.event_type == "tool_call"
        ))
        actual_args = [
            event.payload.tool_args
            for event in events
            if event.event_type == "tool_call"
        ]
        blocked = any(
            event.payload.blocked
            for event in events
            if event.event_type == "tool_result"
        )

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
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "iterations": max(
                    (event.payload.iteration for event in events),
                    default=0,
                ),
                "tool_calls": tool_count,
            },
            actual_tools=actual_tools,
            tool_args=actual_args,
            blocked=blocked,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def _question_config(self, q: BenchmarkQuestion) -> AppConfig:
        config = self._config.model_copy()
        config.service_port = 0
        if q.max_iterations is not None:
            config.max_iterations = q.max_iterations
        if q.max_tool_calls is not None:
            config.max_total_tool_calls = q.max_tool_calls
        return config

    def _create_judge(self) -> LLMJudge | None:
        try:
            model = self._config.resolve_model()
            return LLMJudge(HttpModelProvider(model))
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
