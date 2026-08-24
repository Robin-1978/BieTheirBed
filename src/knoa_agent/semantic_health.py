"""Installer/CI entry point for the Node semantic-recall health probe."""

from __future__ import annotations

import json
import sys

from knoa_agent.tool_selector import verify_semantic_runtime


def main() -> int:
    try:
        result = verify_semantic_runtime(provision="--provision" in sys.argv[1:])
    except Exception as exc:  # noqa: BLE001 - installer boundary
        print(
            json.dumps(
                {"status": "unavailable", "error_code": type(exc).__name__},
                separators=(",", ":"),
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
