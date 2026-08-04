"""Metadata-safe redaction shared by audit and persistence boundaries."""
from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|token|password|passwd|secret|authorization|cookie)", re.I)
_DATA_URL = re.compile(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+")


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _DATA_URL.sub("[binary image omitted]", value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    return value
