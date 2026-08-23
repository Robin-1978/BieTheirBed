"""Cross-platform subprocess byte decoding with auditable raw-byte summaries."""
from __future__ import annotations

import hashlib
import locale
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ProcessOutputSummary:
    encoding: str
    byte_count: int
    sha256: str
    had_replacements: bool

    def as_dict(self) -> dict:
        return asdict(self)


def decode_process_output(raw: bytes, *, windows: bool) -> tuple[str, ProcessOutputSummary]:
    encodings = ["utf-8"]
    if windows:
        preferred = locale.getpreferredencoding(False).lower()
        # gb18030 is a superset of the GBK output emitted by common Chinese
        # Windows tools. Try it before single-byte code pages, which otherwise
        # decode every byte while silently producing mojibake.
        for encoding in ("gb18030", preferred):
            if encoding and encoding not in encodings:
                encodings.append(encoding)
    for encoding in encodings:
        try:
            text = raw.decode(encoding, errors="strict")
            return text, ProcessOutputSummary(
                encoding=encoding, byte_count=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(), had_replacements=False,
            )
        except (LookupError, UnicodeDecodeError):
            continue
    text = raw.decode("utf-8", errors="replace")
    return text, ProcessOutputSummary(
        encoding="utf-8-replace", byte_count=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(), had_replacements=True,
    )


__all__ = ["ProcessOutputSummary", "decode_process_output"]
