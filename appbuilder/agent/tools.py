"""LEARNER-OWNED. Tool schemas and the dispatcher.

The model sees TOOL_SCHEMAS. When it asks for a tool, ``dispatch`` parses and validates the arguments, calls the
right function, and returns a JSON-serialisable result. Failures are returned to the model as data
({"ok": false, "error": ...}) so it can correct itself; they never crash the loop.

The file tools work on a supplied ``Workspace`` (flat names, allow-listed extensions, size limits, nothing outside
``app/``). ``run_checks`` calls the supplied trusted harness on the APPROVED criteria, which the agent cannot read or
edit. ``run_diagnostic`` runs steps the agent proposes and is labelled DIAGNOSTIC: it can never count as acceptance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..supplied.criteria_vocab import VOCABULARY_HELP, validate_criteria
from ..supplied.harness import compact_report
from ..supplied.workspace import WorkspaceError
from .context import RunContext


@dataclass
class BuildState:
    """What the loop tracks between tool calls. Plain data, so you can print it while debugging."""
    last_report: dict | None = None          # last trusted ACCEPTANCE report (full)
    repairs: int = 0                         # repair attempts started after a failed trusted report
    awaiting_repair: bool = False            # a failed report exists and no file has been written since
    diagnostics_used: int = 0
    writes: int = 0
    roles: dict = field(default_factory=dict)  # criterion id -> relevant|regression (revisions)


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}}


TOOL_SCHEMAS = [
    _schema("list_files", "List the app's files with their sizes.", {}, []),
    _schema("read_file", "Read one app file.", {"path": {"type": "string", "description": "flat file name, e.g. index.html"}}, ["path"]),
    _schema("write_file", "Create or replace one app file with the full contents. Allowed: .html .css .js, flat names.",
            {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _schema("delete_file", "Delete one app file.", {"path": {"type": "string"}}, ["path"]),
    _schema("run_checks", "Run ALL approved acceptance criteria against the current files in a real browser. This is the only "
            "check that counts as success. Returns pass/fail per criterion with the reason.", {}, []),
    _schema("run_diagnostic", "Run steps you propose to investigate a problem. Results are DIAGNOSTIC ONLY and never count as "
            "success. " + VOCABULARY_HELP.split("Top level")[0],
            {"title": {"type": "string"}, "steps": {"type": "array", "items": {"type": "object"}}}, ["steps"]),
]
TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]
WRITE_TOOLS = {"write_file", "delete_file"}


def parse_args(raw: object) -> tuple[dict | None, str | None]:
    if isinstance(raw, dict):
        return raw, None
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        return None, "arguments were not valid JSON"
    if not isinstance(args, dict):
        return None, "arguments must be a JSON object"
    return args, None


def dispatch(name: str, raw_args: object, ctx: RunContext, state: BuildState) -> dict:
    if name not in TOOL_NAMES:
        return {"ok": False, "error": f"unknown tool {name!r}; available: {TOOL_NAMES}"}
    args, err = parse_args(raw_args)
    if err:
        return {"ok": False, "error": err}
    ws = ctx.workspace
    try:
        if name == "list_files":
            return {"ok": True, "files": ws.list_files()}
        if name == "read_file":
            return {"ok": True, "path": args.get("path"), "content": ws.read_file(args.get("path"))}
        if name == "write_file":
            r = ws.write_file(args.get("path"), args.get("content"))
            state.writes += 1
            if state.awaiting_repair:                    # first write after a failed trusted report = one repair attempt
                state.repairs += 1
                state.awaiting_repair = False
                ctx.tracer.event("repair_started", repair=state.repairs, of=ctx.limits.max_repairs)
            return {"ok": True, **r}
        if name == "delete_file":
            ws.delete_file(args.get("path"))
            state.writes += 1
            return {"ok": True, "deleted": args.get("path")}
        if name == "run_checks":
            return run_trusted_checks(ctx, state)
        if name == "run_diagnostic":
            return run_diagnostic(ctx, state, args)
    except WorkspaceError as e:
        return {"ok": False, "error_code": e.code, "error": e.message}
    return {"ok": False, "error": "unhandled tool"}  # pragma: no cover


def run_trusted_checks(ctx: RunContext, state: BuildState) -> dict:
    """Run the approved criteria. The result is what status is derived from; the model's words are not."""
    approved = ctx.store.approved(ctx.pid)
    if not approved:
        return {"ok": False, "error": "no approved criteria exist yet"}
    report = ctx.run_checks(ctx.app_dir, approved["criteria"], ctx.limits, roles=state.roles or None, kind="acceptance")
    state.last_report = report
    state.awaiting_repair = not report["all_passed"]
    ctx.store.runs_dir(ctx.pid).mkdir(exist_ok=True)
    from ..supplied.store import write_json
    write_json(ctx.store.runs_dir(ctx.pid) / "last_acceptance.json", report)
    ctx.tracer.event("check_report", kind="acceptance", all_passed=report["all_passed"], summary=report["summary"],
                     failed=[r["id"] for r in report["results"] if r["status"] != "passed"],
                     blocked_requests=len(report["blocked_requests"]))
    return {"ok": True, **compact_report(report)}


def run_diagnostic(ctx: RunContext, state: BuildState, args: dict) -> dict:
    if state.diagnostics_used >= ctx.limits.max_diagnostics:
        return {"ok": False, "error": f"diagnostic limit reached ({ctx.limits.max_diagnostics} per build)"}
    steps = args.get("steps")
    crit = {"criteria": [{"id": "C1", "title": str(args.get("title") or "diagnostic")[:80],
                          "requirement_ids": ["diagnostic"], "steps": steps}]}
    problems = validate_criteria(crit, ctx.workspace.page_names(), ctx.limits)
    if problems:
        return {"ok": False, "error": "diagnostic steps are not valid", "problems": problems[:6]}
    state.diagnostics_used += 1
    report = ctx.run_checks(ctx.app_dir, crit, ctx.limits, kind="diagnostic")
    ctx.tracer.event("check_report", kind="diagnostic", all_passed=report["all_passed"], summary=report["summary"])
    return {"ok": True, **compact_report(report), "label": "DIAGNOSTIC ONLY - does not count toward acceptance"}
