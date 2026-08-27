from __future__ import annotations

import asyncio
from pathlib import Path

from knoa_codex_agent.exec_json import CodexExecJsonClient


def _fake_codex(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json, sys
print(json.dumps({'type':'argv','argv':sys.argv[1:]}), flush=True)
print(json.dumps({'type':'thread.started','thread_id':'thread-json'}), flush=True)
print(json.dumps({'type':'turn.started'}), flush=True)
print(json.dumps({'type':'item.completed','item':{'id':'m1','type':'agent_message','text':'OK'}}), flush=True)
print(json.dumps({'type':'turn.completed','status':'completed','usage':{'input_tokens':2,'output_tokens':1}}), flush=True)
""",
        encoding="utf-8",
    )
    path.chmod(0o700)


def test_exec_json_client_reads_jsonl_and_resume_arguments(tmp_path: Path) -> None:
    executable = tmp_path / "codex"
    _fake_codex(executable)

    async def run() -> None:
        client = CodexExecJsonClient((str(executable),), cwd=str(tmp_path))
        await client.start("hello")
        assert await client.wait_thread_started() == "thread-json"
        events = [event async for event in client.events()]
        argv = events[0]["argv"]
        assert argv[:3] == ["exec", "--skip-git-repo-check", "--json"]
        assert "--sandbox" in argv
        await client.close()

        resumed = CodexExecJsonClient((str(executable),), cwd=str(tmp_path))
        await resumed.start("next", thread_id="thread-json")
        await resumed.wait_thread_started()
        resumed_events = [event async for event in resumed.events()]
        resumed_argv = resumed_events[0]["argv"]
        assert resumed_argv[:2] == ["exec", "resume"]
        assert "thread-json" in resumed_argv
        assert resumed_argv[-1] == "-"
        await resumed.close()

    asyncio.run(run())
