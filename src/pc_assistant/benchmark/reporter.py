from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pc_assistant.benchmark.types import BenchmarkResult


# Dimension weights
WEIGHTS = {
    "text_qa": 0.30,
    "tool_use": 0.25,
    "safety": 0.20,
    "memory": 0.10,
    "robustness": 0.10,
    "efficiency": 0.05,
}

DIMENSION_LABELS = {
    "text_qa": "Text QA (prose)",
    "tool_use": "Tool Use",
    "safety": "Safety",
    "memory": "Memory & Personalization",
    "robustness": "Robustness",
    "efficiency": "Efficiency (auto)",
}


class Reporter:
    @staticmethod
    def generate_report(results: list[BenchmarkResult], output_dir: str) -> str:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        markdown = Reporter._build_markdown(results)
        report_path = output_dir / "report.md"
        report_path.write_text(markdown, encoding="utf-8")
        return str(report_path)

    @staticmethod
    def _build_markdown(results: list[BenchmarkResult]) -> str:
        lines: list[str] = []
        lines.append("# Agent Benchmark Report")
        lines.append("")

        overall, by_dim = Reporter._compute_scores(results)
        lines.append(f"## Overall Score: **{overall:.2f}** / 1.00")
        lines.append("")

        # Per-dimension table
        lines.append("| Dimension | Score | Weight | Weighted | Questions |")
        lines.append("|-----------|-------|--------|----------|-----------|")
        for cat, label in DIMENSION_LABELS.items():
            info = by_dim.get(cat, {"score": 0.0, "count": 0})
            weight = WEIGHTS.get(cat, 0.0)
            lines.append(
                f"| {label} | {info['score']:.2f} | "
                f"{weight:.2f} | {info['score'] * weight:.3f} | "
                f"{info['count']} |"
            )
        lines.append("")

        # Per-subcategory
        lines.append("## By Subcategory")
        lines.append("")
        lines.append("| Dimension | Subcategory | Score | Questions |")
        lines.append("|-----------|-------------|-------|-----------|")
        subcats: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for r in results:
            if r.error:
                continue
            subcats[r.category][r.subcategory].append(r.score)
        for cat in sorted(subcats):
            for subcat in sorted(subcats[cat]):
                scores = subcats[cat][subcat]
                avg = sum(scores) / len(scores) if scores else 0.0
                lines.append(f"| {cat} | {subcat} | {avg:.2f} | {len(scores)} |")
        lines.append("")

        # Per difficulty
        lines.append("## By Difficulty")
        lines.append("")
        diffs: dict[str, list[float]] = defaultdict(list)
        for r in results:
            if r.error:
                continue
            diffs[r.difficulty].append(r.score)
        lines.append("| Difficulty | Score | Questions |")
        lines.append("|------------|-------|-----------|")
        for d in ["easy", "medium", "hard"]:
            scores = diffs.get(d, [])
            avg = sum(scores) / len(scores) if scores else 0.0
            lines.append(f"| {d} | {avg:.2f} | {len(scores)} |")
        lines.append("")

        # Efficiency metrics (average)
        lines.append("## Efficiency Metrics (Average)")
        lines.append("")
        metrics_data: dict[str, list[float]] = defaultdict(list)
        for r in results:
            for key, val in r.metrics.items():
                if isinstance(val, (int, float)):
                    metrics_data[key].append(float(val))
        lines.append("| Metric | Average | Min | Max |")
        lines.append("|--------|---------|-----|-----|")
        for key in sorted(metrics_data):
            vals = metrics_data[key]
            if not vals:
                continue
            avg = sum(vals) / len(vals)
            lines.append(f"| {key} | {avg:.2f} | {min(vals):.2f} | {max(vals):.2f} |")
        lines.append("")

        # Low-score items
        low_items = [r for r in results if r.score < 0.5 and not r.error]
        if low_items:
            lines.append("## Low-Score Items (< 0.5)")
            lines.append("")
            lines.append("| ID | Category | Question | Score | Detail |")
            lines.append("|----|----------|----------|-------|--------|")
            for r in sorted(low_items, key=lambda x: x.score):
                q = r.question[:40].replace("\n", " ") + ("..." if len(r.question) > 40 else "")
                lines.append(
                    f"| {r.question_id} | {r.category} | {q} | "
                    f"{r.score:.2f} | {r.eval_detail} |"
                )
            lines.append("")

        # Errors
        errors = [r for r in results if r.error]
        if errors:
            lines.append("## Errors")
            lines.append("")
            for r in errors:
                lines.append(f"- **{r.question_id}**: {r.error}")

        return "\n".join(lines)

    @staticmethod
    def _compute_scores(results: list[BenchmarkResult]) -> tuple[float, dict[str, dict]]:
        by_dim: dict[str, dict] = {}
        total_weighted = 0.0
        total_weight = 0.0

        for cat in WEIGHTS:
            cat_results = [r for r in results if r.category == cat and not r.error]
            if not cat_results:
                by_dim[cat] = {"score": 0.0, "count": 0}
                continue
            total = sum(r.score for r in cat_results)
            avg = total / len(cat_results)
            by_dim[cat] = {"score": round(avg, 2), "count": len(cat_results)}
            total_weighted += avg * WEIGHTS.get(cat, 0.0)
            total_weight += WEIGHTS.get(cat, 0.0)

        overall = total_weighted / total_weight if total_weight > 0 else 0.0
        return round(overall, 2), by_dim