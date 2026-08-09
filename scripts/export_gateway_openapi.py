"""Export the running Gateway contract for mobile code generation."""
from __future__ import annotations

import json
from pathlib import Path

from pc_assistant.gateway.openapi import gateway_openapi_schema


def main() -> None:
    destination = (
        Path(__file__).resolve().parents[1] / "apps" / "knoa-mobile" / "openapi.json"
    )
    destination.write_text(
        json.dumps(gateway_openapi_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
