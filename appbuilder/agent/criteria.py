"""LEARNER-OWNED. Approved requirements -> structured acceptance criteria (one structured model call + domain checks).

The model proposes; ``check_criteria`` (supplied vocabulary validator + coverage rules) disposes. Problems are sent back
to the model like schema errors. What the vocabulary cannot check is reported as *unverifiable*, never faked.
"""
from __future__ import annotations

from ..supplied.criteria_vocab import VOCABULARY_HELP, validate_criteria
from . import prompts
from .context import RunContext
from .contracts import CriteriaProposal
from .structured import ask_structured


def check_criteria(proposal: CriteriaProposal, scope: dict, limits) -> list[str]:
    pages = [p["filename"] for p in scope["pages"]]
    req_ids = {r["id"] for r in scope["requirements"]}
    nothing_checkable = not proposal.criteria and req_ids and {u.requirement_id for u in proposal.unverifiable} >= req_ids
    # a proposal in which NOTHING can be checked is valid as a proposal; it just cannot be approved until the user resolves it
    problems = [] if nothing_checkable else validate_criteria(proposal.document(), pages, limits)
    covered: set[str] = set()
    for c in proposal.criteria:
        for rid in c.get("requirement_ids", []) if isinstance(c, dict) else []:
            if rid not in req_ids:
                problems.append(f"criterion {c.get('id')}: requirement id {rid} is not in the approved requirements {sorted(req_ids)}")
            covered.add(rid)
    for u in proposal.unverifiable:
        if u.requirement_id not in req_ids:
            problems.append(f"unverifiable entry names unknown requirement {u.requirement_id}")
    problems += variation_problems(proposal.criteria, req_ids)
    missing = sorted(req_ids - covered - {u.requirement_id for u in proposal.unverifiable})
    if missing:
        problems.append(f"requirements {missing} have no criterion; add one, or list them under 'unverifiable' with a reason")
    return problems


INPUT_ACTIONS = ("enter", "select", "filter")


def _steps(c: dict) -> list[dict]:
    """Only well-formed steps. A malformed step (the model wrote a string, or left out a field) is already reported to the model by
    ``validate_criteria``; the variation rule must not crash on it, or that report never reaches the model."""
    return [s for s in c["steps"] if isinstance(s, dict)]


def _signature(c: dict) -> tuple:
    return tuple((s["action"], s.get("target"), s.get("value")) for s in _steps(c) if s.get("action") in INPUT_ACTIONS)


def _expectations(c: dict) -> dict[str, set]:
    """target -> expected values asserted AFTER the criterion has entered/selected/filtered something."""
    seen_input, out = False, {}
    for s in _steps(c):
        if s.get("action") in INPUT_ACTIONS:
            seen_input = True
        elif seen_input and s.get("action") == "assert_text" and "equals" in s:
            out.setdefault(s.get("target"), set()).add(("text", str(s["equals"])))
        elif seen_input and s.get("action") == "assert_count":
            out.setdefault(s.get("target"), set()).add(("count", str(s.get("count"))))
    return out


def variation_problems(criteria: list[dict], req_ids: set[str]) -> list[str]:
    """If what a requirement expects depends on user input, one example is not enough: a page that merely reproduces that
    example would pass. Require two criteria for the same requirement with different inputs AND different expected results.
    This is a proposal-time rule (a bounded mitigation), not proof that test gaming is impossible."""
    problems = []
    for rid in sorted(req_ids):
        cs = [c for c in criteria if isinstance(c, dict) and rid in c.get("requirement_ids", []) and isinstance(c.get("steps"), list)]
        dependent = [c for c in cs if _expectations(c)]
        if not dependent:
            continue
        sigs = {_signature(c) for c in dependent}
        merged: dict[str, set] = {}
        for c in dependent:
            for t, vals in _expectations(c).items():
                merged.setdefault(t, set()).update(vals)
        if len(sigs) < 2 or not any(len(v) >= 2 for v in merged.values()):
            problems.append(f"requirement {rid}: its expected result depends on user input but only one example is checked, so an app that "
                            "reproduces that example would pass. Add a second criterion for the SAME behaviour with different input values "
                            "and a different expected result worked out from the requirement (no new behaviour, no new requirements).")
    return problems


def translate_criteria(ctx: RunContext, scope: dict, *, feedback: str | None = None, keep_context: str | None = None) -> CriteriaProposal:
    system = prompts.CRITERIA_SYSTEM.format(vocab=VOCABULARY_HELP, max_criteria=ctx.limits.max_criteria,
                                            max_steps=ctx.limits.max_steps_per_criterion)
    user = "Scope:\n" + _scope_text(scope)
    if keep_context:
        user += "\n\n" + keep_context
    if feedback:
        user += f"\n\nThe user asked for these changes to the criteria:\n{feedback}"
    ctx.tracer.event("criteria_translation_start", requirements=[r["id"] for r in scope["requirements"]])
    obj = ask_structured(ctx, "criteria", system, user, CriteriaProposal, extra_check=lambda o: check_criteria(o, scope, ctx.limits))
    ctx.tracer.event("criteria_translation_end", criteria=[c["id"] for c in obj.criteria], unverifiable=[u.requirement_id for u in obj.unverifiable])
    return obj


def _scope_text(scope: dict) -> str:
    return ("Pages: " + ", ".join(p["filename"] for p in scope["pages"]) + "\nRequirements:\n"
            + "\n".join(f"{r['id']}: {r['text']}" for r in scope["requirements"]))


def render_unverifiable(items: list[dict], texts: dict[str, str] | None = None) -> str:
    if not items:
        return ""
    lines = ["UNRESOLVED: these requirements cannot be checked automatically. They stay in the project, this proposal cannot be approved "
             "as a completed build, and nothing built for them counts as verified until you choose, for each one, a supported "
             "replacement or to remove it from scope:"]
    for u in items:
        t = (texts or {}).get(u["requirement_id"])
        lines.append(f"  - {u['requirement_id']}" + (f" ({t})" if t else "") + f": {u['reason']}")
        if u.get("proposed_alternative"):
            lines.append(f"      Suggested supported replacement: {u['proposed_alternative']}")
    return "\n".join(lines)
