"""LEARNER-OWNED. Run tracing and live event streaming.

``Tracer.event`` does two things: it appends a redacted record to the project's trace file (the trace
you read after a run), and it forwards the same record to a live sink (the server pushes it to the
browser as server-sent events). Secrets are masked by the supplied Redactor before either happens.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from ..supplied.redact import Redactor


class Tracer:
    def __init__(self, path: Path | None, redactor: Redactor, sink: Callable[[dict], None] | None = None, run_id: str = "run"):
        self.path, self.redactor, self.sink, self.run_id = path, redactor, sink, run_id
        self.events: list[dict] = []

    def event(self, type: str, **data) -> dict:
        rec = self.redactor.obj({"seq": len(self.events) + 1, "ts": round(time.time(), 3), "run": self.run_id,
                                 "type": type, **data})
        self.events.append(rec)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if self.sink:
            try:
                self.sink(rec)
            except Exception:  # a broken listener must never break a build
                pass
        return rec

    def live(self, type: str, **data) -> None:
        """Stream-only event (token deltas): pushed to the sink, not written to the trace file."""
        if self.sink:
            try:
                self.sink(self.redactor.obj({"type": type, "run": self.run_id, **data}))
            except Exception:
                pass
