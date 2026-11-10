"""LEARNER-OWNED. Retry policy for transient model failures.

Retries here are for *transport* problems (rate limit, timeout, network, provider 5xx). They are counted
separately from repair attempts, which are about generated code failing checks. Auth and bad-request errors are
not retried: retrying cannot fix them.
"""
from __future__ import annotations

import time
from typing import Callable

from ..supplied.model_transport import TransportError


def call_with_retry(fn: Callable[[], object], limits, tracer, *, sleep: Callable[[float], None] = time.sleep, what: str = "model call"):
    attempt = 0
    while True:
        try:
            return fn()
        except TransportError as e:
            if not e.retryable or attempt + 1 >= limits.transient_retry_attempts:
                tracer.event("transport_failed", what=what, kind=e.kind, message=e.message, attempts=attempt + 1)
                raise
            delay = min(limits.backoff_cap_s, limits.backoff_base_s * (2 ** attempt))
            if e.retry_after:
                delay = min(limits.backoff_cap_s, max(delay, e.retry_after))
            tracer.event("transport_retry", what=what, kind=e.kind, attempt=attempt + 1, wait_s=delay)
            sleep(delay)
            attempt += 1
