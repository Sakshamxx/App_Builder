"""LEARNER-OWNED. Ask the model for JSON that matches a Pydantic contract.

Default mode is ``prompt_only``: the schema is in the prompt, we parse and validate, and on failure we send
the validation errors back and ask again (bounded by ``limits.structured_retry_attempts``). Provider-enforced
JSON modes (``json_object`` / ``json_schema``) can be switched on via settings once verified with the chosen
provider; the validation loop stays either way because provider enforcement is not a guarantee.
"""
from __future__ import annotations

import json
import re
import time

from pydantic import BaseModel, ValidationError

from .retry import call_with_retry


class StructuredOutputError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors)[:400])
        self.errors = errors


def extract_json(text: str) -> object:
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    if not t.startswith(("{", "[")):
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j > i:
            t = t[i:j + 1]
    return json.loads(t)


def _response_format(mode: str, model_cls: type[BaseModel]):
    if mode == "json_object":
        return {"type": "json_object"}
    if mode == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": model_cls.__name__, "schema": model_cls.model_json_schema()}}
    return None


def ask_structured(ctx, kind: str, system: str, user: str, model_cls: type[BaseModel], *, extra_check=None):
    """Return a validated ``model_cls`` instance. ``extra_check(obj) -> list[str]`` adds domain rules whose problems
    are sent back to the model like schema errors."""
    schema = json.dumps(model_cls.model_json_schema(), separators=(",", ":"))
    messages = [{"role": "system", "content": f"{system}\n\nReply with ONE JSON object and nothing else, matching this JSON Schema:\n{schema}"},
                {"role": "user", "content": user}]
    last_errors: list[str] = []
    for attempt in range(ctx.limits.structured_retry_attempts + 1):
        t0 = time.monotonic()
        ctx.tracer.event("model_call_start", kind=kind, attempt=attempt + 1, messages=len(messages))
        reply = call_with_retry(
            lambda: ctx.transport.chat(messages, on_text=lambda d: ctx.tracer.live("model_text", kind=kind, delta=d),
                                       response_format=_response_format(ctx.settings.structured_mode, model_cls)),
            ctx.limits, ctx.tracer, sleep=ctx.sleep, what=kind)
        ms = int((time.monotonic() - t0) * 1000)
        ctx.usage.record(kind, reply.model, reply.usage, latency_ms=ms)
        ctx.tracer.event("model_call_end", kind=kind, usage=reply.usage, latency_ms=ms, finish_reason=reply.finish_reason)
        raw = reply.content or ""
        try:
            obj = model_cls.model_validate(extract_json(raw))
            problems = extra_check(obj) if extra_check else []
            if not problems:
                return obj
            last_errors = problems
        except (ValueError, ValidationError) as e:
            last_errors = [f"{err['loc']}: {err['msg']}" for err in e.errors()][:8] if isinstance(e, ValidationError) \
                else [f"not valid JSON: {str(e)[:120]}"]
        ctx.tracer.event("structured_invalid", kind=kind, attempt=attempt + 1, errors=last_errors)
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "That reply was not accepted:\n- " + "\n- ".join(last_errors)
                      + "\nReply again with ONE corrected JSON object."}]
    raise StructuredOutputError(last_errors)
