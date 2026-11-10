"""LEARNER-OWNED. The agent loop, written out by hand. This file is the reference for the course.

One build is: ask the model -> run the tools it asks for -> give the results back -> repeat, until a STOP RULE fires.
Nothing here is hidden behind a framework; the LangGraph version in ``appbuilder/framework/graph.py`` has to
reproduce exactly this behaviour.

Stop rules (the first that fires ends the run, and the reason is recorded):
  checks_passed      a trusted acceptance report for the CURRENT files passed every approved criterion
  step_cap           ``max_model_turns`` model calls were used
  repair_cap         ``max_repairs`` repair attempts were used and the trusted checks still fail
  model_stopped      the model stopped calling tools while the current trusted report is failing
  model_unavailable  transient transport failures outlasted the retries      (separate from repairs)
  model_error        a non-retryable provider error (bad key, bad request)

A *repair* is the first file change after a failed trusted report. Transport retries are counted separately.
Success is decided by the trusted report, not by the model saying it is done: if the model stops without a current
report, the loop runs the trusted check itself.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ..supplied.model_transport import TransportError
from ..supplied.restore import promote
from ..supplied.store import contract_hash
from ..supplied.criteria_vocab import render_technical
from . import prompts
from .context import RunContext
from .retry import call_with_retry
from .status import refresh
from .tools import TOOL_SCHEMAS, BuildState, dispatch, run_trusted_checks


@dataclass
class BuildTask:
    kind: str                      # "initial" | "revision"
    user_message: str
    roles: dict = field(default_factory=dict)   # revision: criterion id -> relevant | regression


@dataclass
class BuildOutcome:
    status: str                    # verified | incomplete | error
    stop_reason: str
    turns: int
    repairs: int
    report: dict | None
    failure: dict | None
    promoted: dict | None
    usage: dict

    def to_dict(self) -> dict:
        return {"status": self.status, "stop_reason": self.stop_reason, "turns": self.turns, "repairs": self.repairs,
                "failure": self.failure, "promoted": self.promoted, "usage": self.usage,
                "report_summary": self.report["summary"] if self.report else None}


# ---------------------------------------------------------------------------------------------- messages
def initial_messages(ctx: RunContext, task: BuildTask) -> list[dict]:
    a = ctx.store.approved(ctx.pid)
    scope, crit = a["scope"], a["criteria"]
    system = prompts.BUILD_SYSTEM.format(
        max_files=ctx.limits.max_files,
        requirements="\n".join(f"- {r['id']}: {r['text']}" for r in scope["requirements"]),
        pages="\n".join(f"- {p['filename']}: {p['purpose']}" for p in scope["pages"]),
        criteria_readable=render_technical(crit))
    if task.kind == "revision":
        system = prompts.REVISION_SYSTEM + "\n\n" + system
    return [{"role": "system", "content": system}, {"role": "user", "content": task.user_message}]


def is_current(ctx: RunContext, state: BuildState) -> bool:
    """Is the last trusted report about the files and criteria as they are right now?"""
    r, a = state.last_report, ctx.store.approved(ctx.pid)
    return bool(r and a and r.get("kind") == "acceptance" and r["files_hash"] == ctx.workspace.files_hash() and r["criteria_hash"] == contract_hash(a["criteria"]))


# ---------------------------------------------------------------------------------------------- one model turn
def model_turn(ctx: RunContext, messages: list[dict], turn: int, on_text=None):
    t0 = time.monotonic()
    ctx.tracer.event("model_call_start", kind="build", turn=turn, messages=len(messages))
    reply = call_with_retry(
        lambda: ctx.transport.chat(messages, tools=TOOL_SCHEMAS,
                                   on_text=on_text or (lambda d: ctx.tracer.live("model_text", turn=turn, delta=d))),
        ctx.limits, ctx.tracer, sleep=ctx.sleep, what=f"build turn {turn}")
    ms = int((time.monotonic() - t0) * 1000)
    ctx.usage.record("build", reply.model, reply.usage, latency_ms=ms)
    ctx.tracer.event("model_call_end", kind="build", turn=turn, usage=reply.usage, latency_ms=ms,
                     finish_reason=reply.finish_reason, tool_calls=[c["name"] for c in reply.tool_calls],
                     text=(reply.content or "")[:400])
    return reply


# ---------------------------------------------------------------------------------------------- tool calls
def run_tool_calls(ctx: RunContext, state: BuildState, messages: list[dict], calls: list[dict]) -> None:
    for i, c in enumerate(calls):
        if i >= ctx.limits.max_tool_calls_per_turn:
            result = {"ok": False, "error": f"at most {ctx.limits.max_tool_calls_per_turn} tool calls per turn; this one was not run"}
        else:
            result = dispatch(c["name"], c["arguments"], ctx, state)
        ctx.tracer.event("tool_call", name=c["name"], args=c["arguments"][:300], ok=result.get("ok"),
                         error=result.get("error"))
        messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)})


def after_tools(ctx: RunContext, state: BuildState) -> str | None:
    """Stop rules that depend on the trusted report. Returns a stop reason or None."""
    if state.last_report and is_current(ctx, state):
        if state.last_report["all_passed"]:
            return "checks_passed"
        if state.repairs >= ctx.limits.max_repairs:
            return "repair_cap"
    return None


def when_model_stops(ctx: RunContext, state: BuildState, messages: list[dict]) -> str | None:
    """The model made no tool call. Do not take its word for it."""
    if not is_current(ctx, state):
        ctx.tracer.event("auto_check", reason="model stopped without a current trusted report")
        run_trusted_checks(ctx, state)
        stop = after_tools(ctx, state)
        if stop:
            return stop
        messages.append({"role": "user", "content": "The trusted checks were run for you and some failed:\n"
                         + json.dumps(_compact(state)) + "\nFix the files and call run_checks again."})
        return None
    return "checks_passed" if state.last_report["all_passed"] else "model_stopped"


def _compact(state: BuildState) -> dict:
    from ..supplied.harness import compact_report
    return compact_report(state.last_report)


# ---------------------------------------------------------------------------------------------- the loop
def run_build(ctx: RunContext, task: BuildTask) -> BuildOutcome:
    state = BuildState(roles=task.roles)
    messages = initial_messages(ctx, task)
    ctx.tracer.event("build_start", kind=task.kind, max_model_turns=ctx.limits.max_model_turns, max_repairs=ctx.limits.max_repairs)
    turns, stop, error = 0, None, None
    while stop is None:
        if turns >= ctx.limits.max_model_turns:
            stop = "step_cap"
            break
        turns += 1
        try:
            reply = model_turn(ctx, messages, turns)
        except TransportError as e:
            stop, error = ("model_unavailable" if e.retryable else "model_error"), e.message
            break
        messages.append(reply.assistant_message())
        if reply.tool_calls:
            run_tool_calls(ctx, state, messages, reply.tool_calls)
            stop = after_tools(ctx, state)
        else:
            stop = when_model_stops(ctx, state, messages)
    return finish(ctx, state, task, stop, turns, error)


# ---------------------------------------------------------------------------------------------- finishing
HEADLINES = {
    "step_cap": "Stopped: the step limit ({turns} of {cap} model turns) was reached before the approved checks passed.",
    "repair_cap": "Stopped: {repairs} repair attempts were used and some approved checks still fail.",
    "model_stopped": "Stopped: the model finished without producing a version that passes the approved checks.",
    "model_unavailable": "Stopped: the model provider could not be reached after retries. No repair attempts were used up by this.",
    "model_error": "Stopped: the model provider rejected the request.",
    "framework_backstop": "Stopped: the framework's own recursion limit was hit (this should not happen: the course caps fire first).",
}


def failure_report(ctx: RunContext, state: BuildState, stop: str, turns: int, error: str | None) -> dict:
    failing = []
    if state.last_report:
        failing = [{"id": r["id"], "title": r["title"], "status": r["status"], "message": r.get("message", ""), "role": r.get("role")}
                   for r in state.last_report["results"] if r["status"] != "passed"]
    has_restore = (ctx.store.restore_dir(ctx.pid) / "manifest.json").is_file()
    return {
        "headline": HEADLINES[stop].format(turns=turns, cap=ctx.limits.max_model_turns, repairs=state.repairs),
        "stop_reason": stop, "provider_message": error, "failing_checks": failing,
        "tried": {"model_turns": turns, "repairs": state.repairs, "diagnostics": state.diagnostics_used, "file_writes": state.writes},
        "limits": {"max_model_turns": ctx.limits.max_model_turns, "max_repairs": ctx.limits.max_repairs},
        "status_note": "The app is NOT verified. Do not treat it as working." + (
            " A last-working version is available to restore." if has_restore else " There is no earlier working version to restore."),
        "next_steps": ["Review the failing checks above", "Rephrase or simplify the request and rebuild"]
                      + (["Restore the last working version"] if has_restore else []),
    }


def finish(ctx: RunContext, state: BuildState, task: BuildTask, stop: str, turns: int, error: str | None) -> BuildOutcome:
    promoted, failure = None, None
    if stop == "checks_passed":
        promoted = promote(ctx.store, ctx.pid, state.last_report, ctx.limits)   # supplied deterministic safeguard
        status = "verified" if promoted["promoted"] else "incomplete"
        if not promoted["promoted"]:
            stop = "promotion_refused"
            failure = {"headline": "Checks passed but the result could not be saved as a restore point: " + promoted["reason"],
                       "stop_reason": stop, "failing_checks": [], "status_note": "Not verified."}
    else:
        status = "incomplete" if stop not in ("model_error",) else "error"
        failure = failure_report(ctx, state, stop, turns, error)
    ctx.tracer.event("build_end", status=status, stop_reason=stop, turns=turns, repairs=state.repairs)
    ctx.store.update_meta(ctx.pid, phase="idle", stop_reason=None if status == "verified" else stop, last_build={
        "kind": task.kind, "status": status, "stop_reason": stop, "turns": turns, "repairs": state.repairs,
        "files_hash": ctx.workspace.files_hash(), "failure": failure, "usage": ctx.usage.totals()})
    refresh(ctx.store, ctx.pid, ctx.limits)
    return BuildOutcome(status, stop, turns, state.repairs, state.last_report, failure, promoted, ctx.usage.totals())
