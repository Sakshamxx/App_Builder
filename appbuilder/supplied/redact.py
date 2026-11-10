"""SUPPLIED. Redaction of secrets from anything that may be logged or traced."""
from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
]
MASK = "[REDACTED]"


class Redactor:
    """Masks the configured secrets plus common key shapes. Belt and braces: the main defence is
    that secrets are never passed to code that logs, exports, or executes generated code."""

    def __init__(self, secrets: list[str] | tuple[str, ...] = ()):
        self._secrets = [s for s in secrets if s and len(s) >= 8]

    def text(self, s: str) -> str:
        for secret in self._secrets:
            s = s.replace(secret, MASK)
        for p in _PATTERNS:
            s = p.sub(MASK, s)
        return s

    def obj(self, o: Any) -> Any:
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, dict):
            return {k: self.obj(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [self.obj(v) for v in o]
        return o
