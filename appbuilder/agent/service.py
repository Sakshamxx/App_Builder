"""LEARNER-OWNED. The workflow around the loop: request -> scope -> criteria -> approval -> build -> revise -> restore.

Every model-driven step stops at a user approval gate. Approvals are bound to a hash of what the user was shown, so
approving "this" can never silently approve something else. Contract (requirements + criteria) is only ever written to
``approved/`` through ``store.approve_contract`` after an approval call.
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..supplied import portability, regression
from ..supplied.criteria_vocab import render_readable
from ..supplied.model_transport import TransportError, make_transport
from ..supplied.redact import Redactor
from ..supplied.restore import RestoreError, has_restore_point, promote, restore
from ..supplied.store import ProjectStore, StoreError, contract_hash, now, read_json, write_json
from .context import RunContext
from .criteria import render_unverifiable, translate_criteria
from .loop import BuildOutcome, BuildTask
from .revision import changed_requirement_ids, judge_revision, merge_added, merge_removed, merge_replaced
from .scope import assess_scope, render_scope
from .status import derive_status, refresh
from .structured import StructuredOutputError
from .tools import BuildState, run_trusted_checks
from .trace import Tracer
from .usage import UsageLedger


def _default_transport(settings):
    if settings.provider == "demo":
        from ..testing.demo import DemoTransport
        return DemoTransport()
    return make_transport(settings)


class Busy(Exception):
    pass


class UserError(Exception):
    """A problem the user can act on (shown as-is)."""


def _engine(name: str) -> Callable[[RunContext, BuildTask], BuildOutcome]:
    if name == "framework":
        from ..framework.graph import run_build
    else:
        from .loop import run_build
    return run_build


class AppBuilderService:
    def __init__(self, settings: Settings, store: ProjectStore | None = None, *, transport_factory=None, sleep=None, run_checks=None):
        self.settings = settings
        self.store = store or ProjectStore(settings.data_dir)
        self._transport_factory = transport_factory or _default_transport
        self._sleep = sleep
        self._run_checks = run_checks
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.sinks: dict[str, list[Callable[[dict], None]]] = {}

    # ------------------------------------------------------------------ plumbing
    def _ctx(self, pid: str) -> RunContext:
        s = self.settings
        sinks = self.sinks.setdefault(pid, [])
        tracer = Tracer(self.store.runs_dir(pid) / "trace.jsonl", Redactor(s.secrets()),
                        lambda ev: [f(ev) for f in list(sinks)], run_id=uuid.uuid4().hex[:8])
        usage = UsageLedger.load(self.store.runs_dir(pid) / "usage.jsonl", s.price_input_per_m, s.price_output_per_m)
        kw = {}
        if self._sleep:
            kw["sleep"] = self._sleep
        if self._run_checks:
            kw["run_checks"] = self._run_checks
        try:
            transport = self._transport_factory(s)
        except TransportError as e:
            raise UserError(e.message)
        return RunContext(settings=s, store=self.store, pid=pid, transport=transport, tracer=tracer, usage=usage, **kw)

    def _locked(self, pid: str):
        with self._guard:
            lock = self._locks.setdefault(pid, threading.Lock())
        if not lock.acquire(blocking=False):
            raise Busy("this project is busy with another operation")
        return lock

    def _pending(self, pid: str) -> dict | None:
        return read_json(self.store.runs_dir(pid) / "pending.json")

    def _set_pending(self, pid: str, pending: dict | None, phase: str) -> None:
        p = self.store.runs_dir(pid) / "pending.json"
        if pending is None:
            p.unlink(missing_ok=True)
        else:
            write_json(p, pending)
        self.store.update_meta(pid, phase=phase)
        refresh(self.store, pid, self.settings.limits)

    def _own(self, owner: str, pid: str) -> dict:
        return self.store.get_for_owner(owner, pid)

    def _guarded(self, ctx: RunContext, fn):
        """Run a model step; model/transport failures become clear user-facing errors, never partial state."""
        try:
            return fn()
        except StructuredOutputError as e:
            raise UserError("The model could not produce a valid answer after retries: " + "; ".join(e.errors)[:300])
        except TransportError as e:
            raise UserError(f"The model provider failed ({e.kind}): {e.message}")

    # ------------------------------------------------------------------ 1. request -> scope
    def create(self, owner: str, request: str) -> dict:
        request = (request or "").strip()
        if not request:
            raise UserError("Describe the app you want.")
        if len(request) > self.settings.limits.max_request_chars:
            raise UserError(f"Keep the description under {self.settings.limits.max_request_chars} characters.")
        pid = self.store.create(owner, request[:60])
        write_json(self.store.runs_dir(pid) / "request.json", {"request": request})
        return self.assess(owner, pid)

    def assess(self, owner: str, pid: str, feedback: str | None = None) -> dict:
        self._own(owner, pid)
        lock = self._locked(pid)
        try:
            req = read_json(self.store.runs_dir(pid) / "request.json")["request"]
            text = req + (f"\n\nChange requested to the proposed scope: {feedback}" if feedback else "")
            ctx = self._ctx(pid)
            scope = self._guarded(ctx, lambda: assess_scope(ctx, text))
            d = scope.model_dump()
            if not scope.supported:
                self._set_pending(pid, {"kind": "unsupported", "scope": d, "readable": _unsupported_text(d)}, "unsupported")
            else:
                self.store.update_meta(pid, title=scope.app_title)
                self._set_pending(pid, {"kind": "scope", "scope": d, "scope_hash": contract_hash(d), "readable": render_scope(d)},
                                  "awaiting_scope_approval")
            return self.state(owner, pid)
        finally:
            lock.release()

    def accept_alternative(self, owner: str, pid: str) -> dict:
        p = self._pending(pid)
        if not p or p["kind"] != "unsupported" or not p["scope"].get("proposed_alternative"):
            raise UserError("There is no alternative to accept.")
        write_json(self.store.runs_dir(pid) / "request.json", {"request": p["scope"]["proposed_alternative"],
                                                              "from_unsupported": True})
        return self.assess(owner, pid)

    # ------------------------------------------------------------------ 2. scope approval -> criteria proposal
    def approve_scope(self, owner: str, pid: str, scope_hash: str) -> dict:
        self._own(owner, pid)
        p = self._pending(pid)
        if not p or p["kind"] != "scope":
            raise UserError("There is no scope waiting for approval.")
        if scope_hash != p["scope_hash"]:
            raise UserError("The scope changed since you reviewed it. Review it again.")
        lock = self._locked(pid)
        try:
            ctx = self._ctx(pid)
            self.store.update_meta(pid, scope_approved_hash=scope_hash)
            self._propose_criteria(ctx, p["scope"], "criteria")
            return self.state(owner, pid)
        finally:
            lock.release()

    def _unresolved_items(self, unv: list[dict], scope: dict, origin: str) -> list[dict]:
        texts = {r["id"]: r["text"] for r in scope["requirements"]}
        return [{"requirement_id": u["requirement_id"], "reason": u["reason"], "text": texts.get(u["requirement_id"], ""),
                 "proposed_alternative": u.get("proposed_alternative"), "origin": origin} for u in unv]

    def _proposal_pending(self, kind: str, scope: dict, doc: dict, unresolved: list[dict], extra: dict | None = None) -> dict:
        texts = {r["id"]: r["text"] for r in scope["requirements"]}
        unv = [{k: u.get(k) for k in ("requirement_id", "reason", "proposed_alternative", "text")} for u in unresolved]
        readable = render_readable(doc, scope) + ("\n\n" + render_unverifiable(unv, texts) if unv else "")
        return {"kind": kind, "scope": scope, "criteria": doc, "unverifiable": unv, "criteria_hash": contract_hash(doc),
                "readable": readable, **(extra or {})}

    def _propose_criteria(self, ctx: RunContext, scope: dict, kind: str, *, feedback: str | None = None, extra: dict | None = None):
        prop = self._guarded(ctx, lambda: translate_criteria(ctx, scope, feedback=feedback))
        doc = prop.document()
        unresolved = self._unresolved_items([u.model_dump() for u in prop.unverifiable], scope, "initial" if kind == "criteria" else "revision")
        self.store.update_meta(ctx.pid, unresolved=unresolved)      # durable: survives replaced proposals, restore, export/import
        self._set_pending(ctx.pid, self._proposal_pending(kind, scope, doc, unresolved, extra),
                          "awaiting_criteria_approval" if kind == "criteria" else "awaiting_revision_approval")

    def _refuse_if_unresolved(self, pid: str) -> None:
        un = self.store.unresolved(pid)
        if un:
            raise UserError("These requirements cannot be checked yet: " + ", ".join(u["requirement_id"] for u in un)
                            + ". Choose a supported replacement or remove each from scope; nothing can be approved or saved as a completed build until then.")

    # ------------------------------------------------------------------ 2b. resolving unverifiable requirements (user decisions only)
    def resolve_item(self, owner: str, pid: str, requirement_id: str, action: str, replacement: str | None = None) -> dict:
        """The user's decision about one requirement that cannot be checked: 'replace' (with a supported requirement they
        approve, e.g. the suggested one) or 'remove' (explicitly out of scope). There is no 'keep and approve'."""
        self._own(owner, pid)
        item = next((u for u in self.store.unresolved(pid) if u["requirement_id"] == requirement_id), None)
        p = self._pending(pid)
        if not item:
            raise UserError("That requirement is not waiting for a decision.")
        if action not in ("replace", "remove"):
            raise UserError("Choose 'replace' (with a supported requirement) or 'remove' (out of scope).")
        if not p or p["kind"] not in ("criteria", "revision"):
            raise UserError("There is no proposal to change, so the requirement stays unresolved.")
        text = (replacement or "").strip()
        if action == "replace" and not (3 <= len(text) <= 500):
            raise UserError("Write the supported replacement requirement (3 to 500 characters).")
        lock = self._locked(pid)
        try:
            ctx = self._ctx(pid) if action == "replace" else None
            rest = [u for u in self.store.unresolved(pid) if u["requirement_id"] != requirement_id]
            decision = {"requirement_id": requirement_id, "action": action, "original_text": item.get("text", ""),
                        "reason": item["reason"], "replacement_text": text if action == "replace" else None, "decided_at": now()}
            extra = {k: p[k] for k in ("revision_text", "touched") if k in p}
            extra["resolutions"] = (p.get("resolutions") or []) + [decision]
            if action == "remove":
                scope2, crit2 = merge_removed(p["scope"], p["criteria"], requirement_id)
                if not scope2["requirements"] or not crit2["criteria"]:
                    raise UserError("That would leave nothing to build or check. Replace it instead, or start a new request.")
                a = self.store.approved(pid)
                if p["kind"] == "revision" and a and scope2 == a["scope"] and crit2 == a["criteria"] and not rest:
                    self.store.update_meta(pid, unresolved=[])
                    self._set_pending(pid, None, "idle")           # nothing is left of the proposed change
                    return self.state(owner, pid)
                new_unres = rest
            else:
                sub = {**p["scope"], "requirements": [{"id": requirement_id, "text": text}]}
                prop = self._guarded(ctx, lambda: translate_criteria(ctx, sub))
                scope2, crit2 = merge_replaced(p["scope"], p["criteria"], requirement_id, text, prop.document())
                new_unres = rest + self._unresolved_items([u.model_dump() for u in prop.unverifiable], scope2,
                                                          item.get("origin", "initial"))
                if p["kind"] == "revision":
                    extra["touched"] = sorted(set(extra.get("touched", [])) | {requirement_id})
            self.store.update_meta(pid, unresolved=new_unres)
            self._set_pending(pid, self._proposal_pending(p["kind"], scope2, crit2, new_unres, extra), p_phase(p["kind"]))
            return self.state(owner, pid)
        finally:
            lock.release()

    def revise_proposal(self, owner: str, pid: str, feedback: str) -> dict:
        """The user asks for changes to the proposal they are looking at (scope or criteria) before approving."""
        self._own(owner, pid)
        p = self._pending(pid)
        if not p or p["kind"] not in ("scope", "criteria", "revision"):
            raise UserError("There is nothing to revise.")
        if p["kind"] == "scope":
            return self.assess(owner, pid, feedback)
        lock = self._locked(pid)
        try:
            ctx = self._ctx(pid)
            self._propose_criteria(ctx, p["scope"], p["kind"], feedback=feedback,
                                   extra={k: p[k] for k in ("revision_text", "touched", "resolutions") if k in p})
            return self.state(owner, pid)
        finally:
            lock.release()

    # ------------------------------------------------------------------ 3. criteria approval -> build
    def approve_criteria(self, owner: str, pid: str, criteria_hash: str, *, run: bool = True) -> dict:
        self._own(owner, pid)
        p = self._pending(pid)
        if not p or p["kind"] != "criteria":
            raise UserError("There are no criteria waiting for approval.")
        if criteria_hash != p["criteria_hash"]:
            raise UserError("The criteria changed since you reviewed them. Review them again.")
        self._refuse_if_unresolved(pid)
        self.store.approve_contract(pid, p["scope"], p["criteria"], source="user_approval", resolutions=p.get("resolutions"))
        self._set_pending(pid, None, "idle")
        if not run:
            return self.state(owner, pid)
        if self.store.meta(pid).get("imported"):
            return self.recheck(owner, pid)
        return self.build(owner, pid)

    def build(self, owner: str, pid: str) -> dict:
        self._own(owner, pid)
        if not self.store.approved(pid):
            raise UserError("Approve the requirements and criteria first.")
        self._refuse_if_unresolved(pid)
        lock = self._locked(pid)
        try:
            ctx = self._ctx(pid)
            a = self.store.approved(pid)
            self.store.update_meta(pid, phase="building")
            task = BuildTask("initial", "Build the app described by the approved requirements and pages. "
                             "Start by writing the files, then call run_checks.")
            out = _engine(self.settings.implementation)(ctx, task)
            return {**self.state(owner, pid), "outcome": out.to_dict()}
        finally:
            lock.release()

    # ------------------------------------------------------------------ 4. revisions
    def revise(self, owner: str, pid: str, request: str) -> dict:
        self._own(owner, pid)
        a = self.store.approved(pid)
        if not a:
            raise UserError("Approve requirements and criteria before requesting revisions.")
        self._refuse_if_unresolved(pid)
        request = (request or "").strip()
        if not request or len(request) > self.settings.limits.max_request_chars:
            raise UserError("Describe the change in under %d characters." % self.settings.limits.max_request_chars)
        lock = self._locked(pid)
        try:
            ctx = self._ctx(pid)
            j = self._guarded(ctx, lambda: judge_revision(ctx, a["scope"], request))
            if j.verdict == "unsupported":
                self._set_pending(pid, {"kind": "revision_unsupported", "explanation": j.explanation,
                                        "readable": "Not possible as a browser-only app: " + j.explanation}, "idle")
                return {**self.state(owner, pid), "revision": {"verdict": "unsupported", "explanation": j.explanation}}
            if j.verdict == "conflict":
                self._set_pending(pid, {"kind": "clarification", "question": j.clarification_question, "explanation": j.explanation,
                                        "conflicting": j.conflicting_requirement_ids, "revision_text": request,
                                        "readable": j.clarification_question}, "needs_clarification")
                return {**self.state(owner, pid), "revision": {"verdict": "conflict", "question": j.clarification_question}}
            if j.added_requirements:
                added = [r.model_dump() for r in j.added_requirements]
                sub_scope = {**a["scope"], "requirements": added}
                prop = self._guarded(ctx, lambda: translate_criteria(ctx, sub_scope))
                scope2, crit2 = merge_added(a["scope"], a["criteria"], added, prop.document())
                unresolved = self._unresolved_items([u.model_dump() for u in prop.unverifiable], scope2, "revision")
                self.store.update_meta(pid, unresolved=unresolved)
                self._set_pending(pid, self._proposal_pending("revision", scope2, crit2, unresolved, {
                    "revision_text": request, "touched": j.touched_requirement_ids + [r["id"] for r in added]}),
                    "awaiting_revision_approval")
                return {**self.state(owner, pid), "revision": {"verdict": "compatible_needs_approval"}}
            return self._run_revision(ctx, owner, pid, request, j.touched_requirement_ids)
        finally:
            lock.release()

    def _run_revision(self, ctx: RunContext, owner: str, pid: str, request: str, touched: list[str]) -> dict:
        a = self.store.approved(pid)
        roles = regression.plan(a["criteria"], touched)
        self.store.update_meta(pid, phase="building")
        files = ", ".join(f["path"] for f in ctx.workspace.list_files())
        task = BuildTask("revision", f"Existing files: {files}\nChange requested by the user:\n{request}\n"
                         "Read the files you need, make the smallest change, then call run_checks.", roles)
        out = _engine(self.settings.implementation)(ctx, task)
        return {**self.state(owner, pid), "outcome": out.to_dict(), "revision": {"verdict": "compatible", "roles": roles}}

    def approve_revision(self, owner: str, pid: str, criteria_hash: str) -> dict:
        self._own(owner, pid)
        p = self._pending(pid)
        if not p or p["kind"] != "revision":
            raise UserError("There is no revision waiting for approval.")
        if criteria_hash != p["criteria_hash"]:
            raise UserError("The criteria changed since you reviewed them. Review them again.")
        self._refuse_if_unresolved(pid)
        lock = self._locked(pid)
        try:
            old = self.store.approved(pid)["scope"]
            self.store.approve_contract(pid, p["scope"], p["criteria"], source="user_approval_revision", resolutions=p.get("resolutions"))
            self._set_pending(pid, None, "idle")
            touched = sorted(set(p.get("touched", [])) | set(changed_requirement_ids(old, p["scope"])))
            return self._run_revision(self._ctx(pid), owner, pid, p["revision_text"], touched)
        finally:
            lock.release()

    def resolve_clarification(self, owner: str, pid: str, choice: str) -> dict:
        """choice: 'keep' (drop the change) or 'replace' (the new request replaces the conflicting requirement)."""
        self._own(owner, pid)
        p = self._pending(pid)
        if not p or p["kind"] != "clarification":
            raise UserError("There is nothing to clarify.")
        if choice == "keep":
            self._set_pending(pid, None, "idle")
            return self.state(owner, pid)
        if choice != "replace":
            raise UserError("Choose 'keep' or 'replace'.")
        lock = self._locked(pid)
        try:
            ctx = self._ctx(pid)
            a = self.store.approved(pid)
            new_scope = self._guarded(ctx, lambda: assess_scope(ctx, p["revision_text"] + "\n(The user confirms this REPLACES the conflicting requirement(s) "
                                                                + ", ".join(p["conflicting"]) + ".)", current_scope=a["scope"]))
            if not new_scope.supported:
                raise UserError("The updated request is not supported: " + "; ".join(u.reason for u in new_scope.unsupported))
            d = new_scope.model_dump()
            self._propose_criteria(ctx, d, "revision", extra={"revision_text": p["revision_text"],
                                                              "touched": changed_requirement_ids(a["scope"], d)})
            return self.state(owner, pid)
        finally:
            lock.release()

    # ------------------------------------------------------------------ 5. restore, export, import
    def restore_last_working(self, owner: str, pid: str) -> dict:
        self._own(owner, pid)
        lock = self._locked(pid)
        try:
            res = restore(self.store, pid, self.settings.limits)
            write_json(self.store.runs_dir(pid) / "last_acceptance.json", res["report"])
            if self.store.unresolved(pid):      # a proposal with unresolved requirements is not discarded by restoring files
                refresh(self.store, pid, self.settings.limits)
            else:
                self._set_pending(pid, None, "idle")
            self.store.update_meta(pid, stop_reason=None, last_build=None)
            refresh(self.store, pid, self.settings.limits)
            return self.state(owner, pid)
        except RestoreError as e:
            raise UserError(str(e))
        finally:
            lock.release()

    def export(self, owner: str, pid: str) -> bytes:
        self._own(owner, pid)
        return portability.export_project(self.store, pid, self.settings.limits)

    def import_archive(self, owner: str, data: bytes) -> dict:
        try:
            info = portability.import_project(self.store, owner, data, self.settings.limits)
        except portability.ImportRejected as e:
            raise UserError("Import rejected: " + e.reason)
        pid = info["id"]
        prop = read_json(self.store.imported_dir(pid) / "proposal.json")["proposal"]
        if prop:
            sc, cr = prop["scope"], prop["criteria"]
            pend = self._proposal_pending("criteria", sc, cr, self.store.unresolved(pid), {"imported": True})
            pend["readable"] = ("IMPORTED, NOT TRUSTED. Review these criteria before approving; checks are re-run after approval.\n\n"
                                + pend["readable"])
            self._set_pending(pid, pend, "awaiting_criteria_approval")
        else:
            refresh(self.store, pid, self.settings.limits)
        return {**self.state(owner, pid), "import": info}

    def recheck(self, owner: str, pid: str) -> dict:
        """Run the trusted checks over the current files (no model). Used after an import is re-approved."""
        self._own(owner, pid)
        lock = self._locked(pid)
        try:
            ctx = self._ctx_no_model(pid)
            state = BuildState()
            res = run_trusted_checks(ctx, state)
            if not res.get("ok"):
                raise UserError(res.get("error", "checks could not run"))
            promoted = promote(self.store, pid, state.last_report, self.settings.limits)
            self.store.update_meta(pid, phase="idle", stop_reason=None if state.last_report["all_passed"] else "checks_failed",
                                   last_build={"kind": "recheck", "status": "verified" if state.last_report["all_passed"] else "incomplete",
                                               "stop_reason": "checks_passed" if state.last_report["all_passed"] else "checks_failed",
                                               "files_hash": ctx.workspace.files_hash(), "failure": None, "usage": ctx.usage.totals()})
            refresh(self.store, pid, self.settings.limits)
            return {**self.state(owner, pid), "recheck": {"all_passed": state.last_report["all_passed"], "promoted": promoted}}
        finally:
            lock.release()

    def _ctx_no_model(self, pid: str) -> RunContext:
        tracer = Tracer(self.store.runs_dir(pid) / "trace.jsonl", Redactor(self.settings.secrets()))
        kw = {"run_checks": self._run_checks} if self._run_checks else {}
        return RunContext(settings=self.settings, store=self.store, pid=pid, transport=None, tracer=tracer,
                          usage=UsageLedger.load(self.store.runs_dir(pid) / "usage.jsonl"), **kw)

    # ------------------------------------------------------------------ views
    def list(self, owner: str) -> list[dict]:
        return self.store.list(owner)

    def delete(self, owner: str, pid: str) -> None:
        self._own(owner, pid)
        self.store.delete(pid)

    def state(self, owner: str, pid: str) -> dict:
        meta = self._own(owner, pid)
        lim = self.settings.limits
        st = derive_status(self.store, pid, lim)
        a = self.store.approved(pid)
        ws_files = [f["path"] for f in self._ctx_no_model(pid).workspace.list_files()]
        return {
            "id": pid, "title": meta["title"], "status": st, "pending": self._pending(pid),
            "approved": ({"scope": a["scope"], "criteria": a["criteria"], "readable": render_readable(a["criteria"], a["scope"]),
                          "approval": a["approval"], "resolutions": a.get("resolutions", []),
                          "scope_text": render_scope(a["scope"])} if a else None),
            "unresolved": self.store.unresolved(pid),
            "files": ws_files, "restore_point": meta.get("restore_point"), "last_build": meta.get("last_build"),
            "usage": UsageLedger.load(self.store.runs_dir(pid) / "usage.jsonl", self.settings.price_input_per_m,
                                      self.settings.price_output_per_m).totals(),
            "limits": {"max_model_turns": lim.max_model_turns, "max_repairs": lim.max_repairs},
            "model": {"provider": self.settings.provider, "model": self.settings.model, "implementation": self.settings.implementation},
        }


def p_phase(kind: str) -> str:
    return "awaiting_criteria_approval" if kind == "criteria" else "awaiting_revision_approval"


def _unsupported_text(scope: dict) -> str:
    lines = ["This request cannot be built as a browser-only app with up to three pages:"]
    lines += [f"  - {u['feature']}: {u['reason']}" for u in scope["unsupported"]]
    if scope.get("proposed_alternative"):
        lines += ["", "A supported alternative: " + scope["proposed_alternative"]]
    return "\n".join(lines)
