"""LEARNER-OWNED. Judging a requested change against the approved requirements (one structured model call).

The judgment is a model decision; what happens next is code: a conflict stops everything and asks the user, a compatible
change runs the revision loop with regression checks, an unsupported change is declined with the reason.
"""
from __future__ import annotations

import json

from . import prompts
from .context import RunContext
from .contracts import RevisionJudgment
from .structured import ask_structured


def judge_revision(ctx: RunContext, scope: dict, request: str) -> RevisionJudgment:
    ids = {r["id"] for r in scope["requirements"]}

    def check(j: RevisionJudgment) -> list[str]:
        bad = [i for i in j.touched_requirement_ids + j.conflicting_requirement_ids if i not in ids]
        problems = [f"unknown approved requirement id {i}" for i in bad]
        problems += [f"added requirement id {r.id} already exists" for r in j.added_requirements if r.id in ids]
        return problems

    user = ("APPROVED requirements:\n" + "\n".join(f"{r['id']}: {r['text']}" for r in scope["requirements"])
            + "\n\nRequested change:\n" + request)
    ctx.tracer.event("revision_judgment_start")
    j = ask_structured(ctx, "revision_judgment", prompts.REVISION_JUDGE_SYSTEM, user, RevisionJudgment, extra_check=check)
    ctx.tracer.event("revision_judgment_end", verdict=j.verdict, touched=j.touched_requirement_ids,
                     conflicting=j.conflicting_requirement_ids)
    return j


def merge_added(scope: dict, criteria: dict, added: list[dict], new_criteria: dict) -> tuple[dict, dict]:
    """Append added requirements and their criteria to the approved ones, renumbering new criteria after the existing."""
    scope2 = json.loads(json.dumps(scope))
    scope2["requirements"] += added
    crit2 = json.loads(json.dumps(criteria))
    n = max(int(c["id"][1:]) for c in crit2["criteria"])
    for c in new_criteria["criteria"]:
        n += 1
        crit2["criteria"].append({**c, "id": f"C{n}"})
    if new_criteria.get("labels"):
        crit2["labels"] = {**new_criteria["labels"], **(crit2.get("labels") or {})}
    if not crit2.get("fixed_time") and new_criteria.get("fixed_time"):
        crit2["fixed_time"] = new_criteria["fixed_time"]
    return scope2, crit2


def changed_requirement_ids(old_scope: dict, new_scope: dict) -> list[str]:
    old = {r["id"]: r["text"] for r in old_scope["requirements"]}
    return [r["id"] for r in new_scope["requirements"] if old.get(r["id"]) != r["text"]]


def _renumber_append(crit: dict, new_doc: dict) -> dict:
    n = max([int(c["id"][1:]) for c in crit["criteria"]] or [0])
    for c in new_doc["criteria"]:
        n += 1
        crit["criteria"].append({**c, "id": f"C{n}"})
    if new_doc.get("labels"):
        crit["labels"] = {**new_doc["labels"], **(crit.get("labels") or {})}
    if not crit.get("fixed_time") and new_doc.get("fixed_time"):
        crit["fixed_time"] = new_doc["fixed_time"]
    return crit


def _strip_requirement(crit: dict, rid: str) -> dict:
    out = []
    for c in crit["criteria"]:
        ids = [i for i in c["requirement_ids"] if i != rid]
        if ids:
            out.append({**c, "requirement_ids": ids})
    return {**crit, "criteria": out}


def merge_replaced(scope: dict, criteria: dict, rid: str, text: str, new_doc: dict) -> tuple[dict, dict]:
    """The user chose a supported replacement for requirement ``rid``: same id, new text, criteria for it appended."""
    scope2 = json.loads(json.dumps(scope))
    for r in scope2["requirements"]:
        if r["id"] == rid:
            r["text"] = text
            break
    else:
        scope2["requirements"].append({"id": rid, "text": text})
    return scope2, _renumber_append(_strip_requirement(json.loads(json.dumps(criteria)), rid), new_doc)


def merge_removed(scope: dict, criteria: dict, rid: str) -> tuple[dict, dict]:
    """The user removed requirement ``rid`` from scope."""
    scope2 = json.loads(json.dumps(scope))
    scope2["requirements"] = [r for r in scope2["requirements"] if r["id"] != rid]
    return scope2, _strip_requirement(json.loads(json.dumps(criteria)), rid)
