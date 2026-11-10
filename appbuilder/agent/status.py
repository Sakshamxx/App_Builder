"""LEARNER-OWNED. Status is derived by code from trusted evidence, never from what the model says.

Workflow phases (``meta.phase``, set by the orchestrator): awaiting_scope_approval, awaiting_criteria_approval,
awaiting_revision_approval, needs_clarification, unsupported, building. Otherwise status comes from the files:

unresolved_requirements  a requirement cannot be checked and the user has not replaced or removed it; takes priority over
              everything below (existing files are only a partial draft, marked incomplete)
verified      the last trusted acceptance report passed every approved criterion for the CURRENT files and CURRENT criteria
incomplete    the last build/revision stopped (cap or failure) and the files have not changed since
failed_checks the last trusted report for the current files did not pass
unverified    files or criteria changed since the last trusted report (or none exists yet)
"""
from __future__ import annotations

from ..supplied.store import contract_hash, read_json
from ..supplied.workspace import Workspace

PHASES = {"awaiting_scope_approval", "awaiting_criteria_approval", "awaiting_revision_approval",
          "needs_clarification", "unsupported", "building"}


def derive_status(store, pid: str, limits) -> dict:
    meta = store.meta(pid)
    unresolved = [u["requirement_id"] for u in meta.get("unresolved") or []]
    if unresolved and meta.get("phase") != "building":
        # never "verified" while a requirement is unresolved; files already present are a partial draft
        return {"status": "unresolved_requirements", "reason": "unresolved requirements: " + ", ".join(unresolved),
                "unresolved": unresolved, "partial_draft": bool(Workspace(store.app_dir(pid), limits).list_files()),
                "waiting_on": meta.get("phase")}
    if meta.get("phase") in PHASES:
        return {"status": meta["phase"], "reason": meta.get("stop_reason")}
    ws = Workspace(store.app_dir(pid), limits)
    approved = store.approved(pid)
    if not approved:
        return {"status": "imported_untrusted" if meta.get("imported") else "draft", "reason": None}
    fh = ws.files_hash()
    lb = meta.get("last_build") or {}
    if not ws.list_files():
        if lb.get("status") == "incomplete":
            return {"status": "incomplete", "reason": lb.get("stop_reason"), "failing": []}
        return {"status": "no_files", "reason": None}
    report = read_json(store.runs_dir(pid) / "last_acceptance.json")
    current = bool(report) and report.get("files_hash") == fh and report.get("criteria_hash") == contract_hash(approved["criteria"])
    if current and report["all_passed"]:
        return {"status": "verified", "reason": None, "wording": "passed the listed checks under these conditions",
                "checks": {"passed": report["summary"]["passed"], "total": len(report["results"])},
                "conditions": report.get("conditions", {})}
    failing = [r["id"] for r in report["results"] if r["status"] != "passed"] if current else []
    if lb.get("status") == "incomplete" and lb.get("files_hash") == fh:
        return {"status": "incomplete", "reason": lb.get("stop_reason"), "failing": failing}
    if current:
        return {"status": "failed_checks", "reason": meta.get("stop_reason"), "failing": failing}
    return {"status": "unverified", "reason": "files or criteria changed since the last trusted check" if report
            else "no trusted check has run"}


def refresh(store, pid: str, limits) -> dict:
    st = derive_status(store, pid, limits)
    store.update_meta(pid, status=st["status"], status_detail=st)
    return st
