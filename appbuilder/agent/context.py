"""LEARNER-OWNED. What one agent run needs, in one place (so the handwritten and framework versions share it)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..supplied import harness
from ..supplied.store import ProjectStore
from ..supplied.workspace import Workspace
from .trace import Tracer
from .usage import UsageLedger


@dataclass
class RunContext:
    settings: Settings
    store: ProjectStore
    pid: str
    transport: object                      # anything with .chat(messages, tools=None, on_text=None, ...)
    tracer: Tracer
    usage: UsageLedger
    sleep: Callable[[float], None] = time.sleep
    run_checks: Callable = harness.run_checks   # the supplied trusted harness (replaceable only in tests)
    extras: dict = field(default_factory=dict)

    @property
    def limits(self):
        return self.settings.limits

    @property
    def app_dir(self) -> Path:
        return self.store.app_dir(self.pid)

    @property
    def workspace(self) -> Workspace:
        return Workspace(self.app_dir, self.limits)
