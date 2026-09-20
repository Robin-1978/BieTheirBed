"""Tool-router benchmark (manual run, not a CI gate).

Compares recall approaches on the frozen 84 user-perspective queries against
the real MCP corpus (registry double-prefixed names, live descriptions):

    .venv/bin/python tests/bench_tool_router.py

Contenders: lexical-only, bge (production _document + threshold).
Acceptance: bge top6 >= 79/84.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

QUERIES = [
    ('casual', '帮我打开公司官网瞅一眼', ['mcp__browser__browser_navigate']),
    ('formal', '请访问公司官方网站首页', ['mcp__browser__browser_navigate']),
    ('terse', '公司官网', ['mcp__browser__browser_navigate']),
    ('casual', '现在的页面拍一张我看看', ['mcp__browser__browser_screenshot']),
    ('formal', '请将当前页面截图发我', ['mcp__browser__browser_screenshot']),
    ('terse', '页面截图', ['mcp__browser__browser_screenshot']),
    ('casual', '这页上都有啥能点的东西', ['mcp__browser__browser_snapshot']),
    ('formal', '请告诉我页面上有哪些可交互元素', ['mcp__browser__browser_snapshot']),
    ('terse', '页面有啥', ['mcp__browser__browser_snapshot']),
    ('casual', '帮我点那个登录', ['mcp__browser__browser_click']),
    ('formal', '请点击登录按钮', ['mcp__browser__browser_click']),
    ('terse', '点登录', ['mcp__browser__browser_click']),
    ('casual', '账号密码帮我填上', ['mcp__browser__browser_fill']),
    ('formal', '请在登录框中输入账号和密码', ['mcp__browser__browser_fill']),
    ('terse', '填账号密码', ['mcp__browser__browser_fill']),
    ('casual', '填完了，交吧', ['mcp__browser__browser_submit_form']),
    ('formal', '请提交已填写的表单', ['mcp__browser__browser_submit_form']),
    ('terse', '提交', ['mcp__browser__browser_submit_form']),
    ('casual', '出来结果了叫我', ['mcp__browser__browser_wait_for']),
    ('formal', '页面加载完成后请通知我', ['mcp__browser__browser_wait_for']),
    ('terse', '等加载完', ['mcp__browser__browser_wait_for']),
    ('casual', '这个安装包下一下', ['mcp__browser__browser_download_file']),
    ('formal', '请下载页面上的安装包文件', ['mcp__browser__browser_download_file']),
    ('terse', '下载安装包', ['mcp__browser__browser_download_file']),
    ('casual', '换个干净的浏览器重新来', ['mcp__browser__browser_session_open']),
    ('formal', '请使用全新会话重新打开', ['mcp__browser__browser_session_open']),
    ('terse', '新开浏览器', ['mcp__browser__browser_session_open']),
    ('casual', '浏览器可以关了', ['mcp__browser__browser_session_close']),
    ('formal', '请关闭浏览器', ['mcp__browser__browser_session_close']),
    ('terse', '关浏览器', ['mcp__browser__browser_session_close']),
    ('casual', '上次那个崩溃的单子找出来', ['mcp__jira__jira_search_issues']),
    ('formal', '请查找上周的线上故障工单', ['mcp__jira__jira_search_issues']),
    ('terse', '找故障单', ['mcp__jira__jira_search_issues']),
    ('casual', '这单子现在啥情况了', ['mcp__jira__jira_get_issue']),
    ('formal', '请告知该工单的当前进展', ['mcp__jira__jira_get_issue']),
    ('terse', '单子进展', ['mcp__jira__jira_get_issue']),
    ('casual', '跟他们说我周五给回复', ['mcp__jira__jira_add_comment']),
    ('formal', '请在工单中回复预计周五交付', ['mcp__jira__jira_add_comment']),
    ('terse', '回复周五交付', ['mcp__jira__jira_add_comment']),
    ('casual', '这活给小王跟吧', ['mcp__jira__jira_assign_issue']),
    ('formal', '请将此工单指派给小王', ['mcp__jira__jira_assign_issue']),
    ('terse', '派给小王', ['mcp__jira__jira_assign_issue']),
    ('casual', '这单搞定了，可以关了', ['mcp__jira__jira_transition_issue']),
    ('formal', '请将该工单关闭', ['mcp__jira__jira_transition_issue']),
    ('terse', '关闭工单', ['mcp__jira__jira_transition_issue']),
    ('casual', '这单接下来还能干嘛', ['mcp__jira__jira_list_transitions']),
    ('formal', '请问该工单下一步可执行什么操作', ['mcp__jira__jira_list_transitions']),
    ('terse', '下一步操作', ['mcp__jira__jira_list_transitions']),
    ('casual', '这活能派给谁啊', ['mcp__jira__jira_find_assignable_users']),
    ('formal', '请问哪些人可以承接该工单', ['mcp__jira__jira_find_assignable_users']),
    ('terse', '谁能接单', ['mcp__jira__jira_find_assignable_users']),
    ('casual', '单子里的截图下下来', ['mcp__jira__jira_download_attachment']),
    ('formal', '请下载工单中的截图附件', ['mcp__jira__jira_download_attachment']),
    ('terse', '下附件图', ['mcp__jira__jira_download_attachment']),
    ('casual', '这单的东西都弄下来我瞧瞧', ['mcp__jira__jira_fetch_issue_evidence', 'mcp__jira__jira_download_attachment']),
    ('formal', '请打包下载该工单的全部资料', ['mcp__jira__jira_fetch_issue_evidence']),
    ('terse', '单子资料打包', ['mcp__jira__jira_fetch_issue_evidence']),
    ('casual', '日志里到底为啥挂的', ['mcp__jira__jira_analyze_local_logs']),
    ('formal', '请分析日志中的挂机原因', ['mcp__jira__jira_analyze_local_logs']),
    ('terse', '挂机原因', ['mcp__jira__jira_analyze_local_logs']),
    ('casual', '昨晚那次构建咋样了', ['mcp__gitlab__gitlab_get_pipeline']),
    ('formal', '请查询昨晚构建的执行情况', ['mcp__gitlab__gitlab_get_pipeline']),
    ('terse', '昨晚构建', ['mcp__gitlab__gitlab_get_pipeline']),
    ('casual', '那次构建里都跑了啥', ['mcp__gitlab__gitlab_list_pipeline_jobs']),
    ('formal', '请列出该构建包含的任务', ['mcp__gitlab__gitlab_list_pipeline_jobs']),
    ('terse', '构建任务列表', ['mcp__gitlab__gitlab_list_pipeline_jobs']),
    ('casual', '编译那个跑完了吗', ['mcp__gitlab__gitlab_get_job']),
    ('formal', '请查询编译任务是否完成', ['mcp__gitlab__gitlab_get_job']),
    ('terse', '编译完了吗', ['mcp__gitlab__gitlab_get_job']),
    ('casual', '编译为啥又失败了', ['mcp__gitlab__gitlab_get_job_trace']),
    ('formal', '请查看编译失败的日志', ['mcp__gitlab__gitlab_get_job_trace']),
    ('terse', '编译失败原因', ['mcp__gitlab__gitlab_get_job_trace']),
    ('casual', '再跑一次试试吧', ['mcp__gitlab__gitlab_retry_job', 'mcp__gitlab__gitlab_retry_oom_jobs']),
    ('formal', '请重新执行失败的构建任务', ['mcp__gitlab__gitlab_retry_job']),
    ('terse', '重跑构建', ['mcp__gitlab__gitlab_retry_job', 'mcp__gitlab__gitlab_retry_oom_jobs']),
    ('casual', '爆内存那几个再跑一遍', ['mcp__gitlab__gitlab_retry_oom_jobs']),
    ('formal', '请重跑内存不足的构建任务', ['mcp__gitlab__gitlab_retry_oom_jobs', 'mcp__gitlab__gitlab_retry_job']),
    ('terse', '重跑爆内存', ['mcp__gitlab__gitlab_retry_oom_jobs']),
    ('casual', '云上都有些啥文件', ['mcp__cloud_files__cloud_files_list_files']),
    ('formal', '请列出云端的全部文件', ['mcp__cloud_files__cloud_files_list_files']),
    ('terse', '文件都有啥', ['mcp__cloud_files__cloud_files_list_files']),
    ('casual', '把云上那个文件拿下来', ['mcp__cloud_files__cloud_files_download_file']),
    ('formal', '请下载云端的文件', ['mcp__cloud_files__cloud_files_download_file']),
    ('terse', '下载云端文件', ['mcp__cloud_files__cloud_files_download_file']),
]

_SERVERS = {
    "jira_mcp_server": "jira",
    "browser_mcp_server": "browser",
    "gitlab_mcp_server": "gitlab",
    "cloud_files_mcp_server": "cloud_files",
}


def load_corpus() -> dict[str, str]:
    """Parse live tool names + descriptions from the example MCP servers."""

    out: dict[str, str] = {}
    for server_dir, short in _SERVERS.items():
        src = (REPO_ROOT / "examples" / server_dir / "server.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                try:
                    items = ast.literal_eval(node.iter)
                except (ValueError, SyntaxError):
                    continue
                for item in items:
                    if (
                        isinstance(item, tuple)
                        and len(item) == 3
                        and isinstance(item[0], str)
                        and "." in item[0]
                    ):
                        out[f"mcp__{short}__{item[0].replace('.', '_')}"] = item[2]
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "Tool"
            ):
                keywords = {
                    keyword.arg: ast.get_source_segment(src, keyword.value)
                    for keyword in node.keywords
                    if keyword.arg in ("name", "description")
                }
                if "name" not in keywords or "description" not in keywords:
                    continue
                try:
                    name = ast.literal_eval(keywords["name"])
                except (ValueError, SyntaxError):
                    continue
                if not isinstance(name, str) or "." not in name:
                    continue
                try:
                    parts = [
                        ast.literal_eval(line)
                        for line in keywords["description"].split("\n")
                        if line.strip().strip("() ")[:1] in ("'", '"')
                    ]
                except (ValueError, SyntaxError):
                    continue
                out[f"mcp__{short}__{name.replace('.', '_')}"] = " ".join(
                    " ".join(parts).split()
                )
    return out


def print_result(
    title: str,
    names: list[str],
    queries: list[tuple[str, str, list[str]]],
    rank_fn,
) -> tuple[int, int, int]:
    t1 = t3 = t6 = 0
    by_style: dict[str, list[int]] = {}
    misses: list[tuple[str, str, str]] = []
    for style, query, expected in queries:
        ranked = rank_fn(query)
        exp = set(expected)
        h1 = bool(ranked) and ranked[0] in exp
        h3 = any(name in exp for name in ranked[:3])
        h6 = any(name in exp for name in ranked[:6])
        row = by_style.setdefault(style, [0, 0, 0, 0])
        row[0] += 1
        row[1] += h1
        row[2] += h3
        row[3] += h6
        if not h6:
            misses.append((style, query, ranked[0] if ranked else "-"))
    n = len(queries)
    t1 = sum(v[1] for v in by_style.values())
    t3 = sum(v[2] for v in by_style.values())
    t6 = sum(v[3] for v in by_style.values())
    print(f"--- {title} ({len(names)} tools, {n} queries): top1={t1}/{n} top3={t3}/{n} top6={t6}/{n}")
    for style, (tot, h1, h3, h6) in by_style.items():
        print(f"    {style:<7} top1={h1}/{tot} top3={h3}/{tot} top6={h6}/{tot}")
    for style, query, got in misses:
        print(f"    MISS [{style}] {query} -> {got}")
    return t1, t3, t6


def main() -> int:
    from knoa_agent.tool_inventory import ToolInventory
    from knoa_agent.tool_selector import BgeToolSelector, tool_tags_for

    corpus = load_corpus()
    print(f"corpus: {len(corpus)} tools")
    names = sorted(corpus)
    candidates = tuple((name, corpus[name]) for name in names)

    # Contender 1: lexical-only (production tokenizer + tags).
    def rank_lexical(query: str) -> list[str]:
        tokens = ToolInventory._tokens(query)
        scored = []
        for name in names:
            tags = " ".join(tool_tags_for(name))
            searchable = ToolInventory._tokens(f"{name} {corpus[name]} {tags}")
            overlap = tokens & searchable
            if overlap:
                scored.append((len(overlap), name))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [name for _score, name in scored]

    print_result("lexical-only", names, QUERIES, rank_lexical)

    # Contender 2: BGE with production documents, full ranking by score.
    import numpy as np

    selector = BgeToolSelector()
    try:
        selector._model = selector._load_model()
    except (ImportError, ModuleNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"bge unavailable, skipped: {type(exc).__name__}")
        return 0

    embeddings = np.asarray(
        list(selector._model.embed([selector._document(n, corpus[n]) for n in names]))
    )

    def rank_bge(query: str) -> list[str]:
        query_embedding = np.asarray(list(selector._model.query_embed(query))[0])
        scores = embeddings @ query_embedding
        order = np.argsort(scores)[::-1]
        return [names[i] for i in order]

    _t1, _t3, t6 = print_result("bge-ranked", names, QUERIES, rank_bge)

    # Agreement: production select() batch must equal threshold+top_k slice.
    agreed = 0
    for _style, query, _expected in QUERIES:
        selection = selector.select(query, candidates)
        query_embedding = np.asarray(list(selector._model.query_embed(query))[0])
        scores = embeddings @ query_embedding
        order = np.argsort(scores)[::-1]
        want = {
            names[i]
            for i in order[: selector._top_k]
            if float(scores[i]) >= selector._threshold
        }
        agreed += selection.names == want
    print(f"bge-production-agreement: {agreed}/{len(QUERIES)}")
    if t6 < 79:
        print(f"ACCEPTANCE FAILED: bge top6 {t6}/84 < 79/84")
        return 1
    print("ACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
