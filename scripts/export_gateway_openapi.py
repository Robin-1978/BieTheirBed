"""Export the running Gateway contract for mobile code generation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
    from knoa_platform.gateway.openapi import gateway_openapi_schema

    destination = (
        REPOSITORY_ROOT / "apps" / "knoa-mobile" / "openapi.json"
    )
    destination.write_text(
        json.dumps(gateway_openapi_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
