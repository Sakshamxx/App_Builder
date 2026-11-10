"""LEARNER-OWNED. Free-form request -> scope assessment (one structured model call)."""
from __future__ import annotations

from . import prompts
from .context import RunContext
from .contracts import ScopeAssessment
from .structured import ask_structured


def assess_scope(ctx: RunContext, request: str, *, current_scope: dict | None = None) -> ScopeAssessment:
    if len(request) > ctx.limits.max_request_chars:
        raise ValueError(f"the request is longer than {ctx.limits.max_request_chars} characters")
    user = f"Request:\n{request}"
    if current_scope:
        user = ("An app already exists with this APPROVED scope (keep the ids and text of requirements that are not being "
                f"changed):\n{current_scope}\n\nThe user now wants this change:\n{request}\n\nReturn the full updated scope.")
    ctx.tracer.event("scope_assessment_start", request_chars=len(request))
    obj = ask_structured(ctx, "scope", prompts.SCOPE_SYSTEM, user, ScopeAssessment)
    ctx.tracer.event("scope_assessment_end", supported=obj.supported, pages=[p.filename for p in obj.pages],
                     unsupported=[u.feature for u in obj.unsupported])
    return obj


def render_scope(scope: dict) -> str:
    lines = [f"{scope['app_title']}: {scope['summary']}", "", "Pages:"]
    lines += [f"  - {p['filename']}: {p['purpose']}" for p in scope["pages"]]
    lines += ["", "Requirements:"] + [f"  {r['id']}: {r['text']}" for r in scope["requirements"]]
    if scope.get("unsupported"):
        lines += ["", "NOT included (cannot be built as a browser-only app):"]
        lines += [f"  - {u['feature']}: {u['reason']}" for u in scope["unsupported"]]
        if scope.get("proposed_alternative"):
            lines.append(f"  Suggested alternative: {scope['proposed_alternative']}")
    return "\n".join(lines)
