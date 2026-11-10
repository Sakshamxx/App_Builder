"""SUPPLIED. The trusted harness API used by the agent's ``run_checks`` tool.

``run_checks`` validates the criteria against the bounded vocabulary, starts the worker process
with a scrubbed environment, collects results as they arrive, and returns one report. Reports use
four outcomes per criterion: passed, failed, errored, not-run. The wording of a good result is
"passed the listed checks under these conditions" (see ``describe_conditions``).
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ..config import Limits
from .criteria_vocab import validate_criteria
from .store import contract_hash
from .workspace import Workspace

PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])
WORKER = str(Path(__file__).with_name("harness_worker.py"))


def _browsers_path() -> str | None:
    p = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if p and Path(p).is_dir():
        return p
    home = Path.home()
    for c in (home / ".cache" / "ms-playwright", home / "Library" / "Caches" / "ms-playwright",
              Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"):
        if c.is_dir():
            return str(c)
    return None


def scrubbed_env(home: str) -> dict[str, str]:
    """The worker's whole environment. No API keys, no inherited variables."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "TMPDIR": home, "LANG": "C.UTF-8"}
    bp = _browsers_path()
    if bp:
        env["PLAYWRIGHT_BROWSERS_PATH"] = bp
    return env


def run_checks(app_dir: Path, criteria: dict, limits: Limits, *, ids: list[str] | None = None,
               roles: dict[str, str] | None = None, kind: str = "acceptance",
               with_csp: bool = True, timeout: float | None = None) -> dict:
    """Run criteria against ``app_dir`` and return a report (never raises for app problems)."""
    ws = Workspace(Path(app_dir), limits)
    files_hash = ws.files_hash()
    crit_hash = contract_hash(criteria)
    problems = validate_criteria(criteria, ws.page_names(), limits)
    base = {"kind": kind, "criteria_hash": crit_hash, "files_hash": files_hash, "all_passed": False,
            "conditions": {"network": "only the app's own origin; everything else aborted and recorded",
                           "storage": "clean browser context per criterion"},
            "blocked_requests": [], "diagnostics": [], "problems": problems}
    all_criteria = criteria.get("criteria", []) if isinstance(criteria, dict) else []
    wanted = [c for c in all_criteria if ids is None or c.get("id") in ids]
    if ids:
        order = {i: n for n, i in enumerate(ids)}
        wanted.sort(key=lambda c: order.get(c["id"], 999))
    results = {c["id"]: {"id": c["id"], "title": c.get("title", ""), "role": (roles or {}).get(c["id"], kind),
                         "requirement_ids": c.get("requirement_ids", []), "status": "not_run",
                         "failed_step": None, "message": "not run"} for c in wanted}
    if problems:
        for r in results.values():
            r.update(status="errored", message="criteria are invalid: " + "; ".join(problems)[:300])
        return _finish(base, results)
    if not wanted:
        base["problems"] = ["no criteria selected"]
        return _finish(base, results)

    if kind == "diagnostic":
        base["conditions"]["note"] = "DIAGNOSTIC ONLY: agent-proposed steps, never counted as acceptance"
    job = {"package_root": PACKAGE_ROOT, "app_dir": str(Path(app_dir).resolve()), "criteria": wanted,
           "step_timeout_ms": limits.step_timeout_ms, "fixed_time": criteria.get("fixed_time"), "with_csp": with_csp}
    deadline = time.monotonic() + (timeout or limits.check_timeout_s)
    with tempfile.TemporaryDirectory(prefix="harness-") as home:
        proc = subprocess.Popen([sys.executable, "-I", WORKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=scrubbed_env(home), cwd=home, text=True,
                                start_new_session=True)
        q: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: [q.put(line) for line in proc.stdout] or q.put(None), daemon=True).start()
        try:
            proc.stdin.write(json.dumps(job))
            proc.stdin.close()
        except BrokenPipeError:
            pass
        fatal = None
        timed_out = False
        done = False
        while not done:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = q.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            t = msg.get("type")
            if t == "conditions":
                base["conditions"].update({k: v for k, v in msg.items() if k != "type"})
            elif t == "result" and msg["id"] in results:
                results[msg["id"]].update(status=msg["status"], failed_step=msg.get("failed_step"),
                                          message=msg.get("message", ""))
            elif t == "done":
                base["blocked_requests"] = msg.get("blocked_requests", [])
                base["diagnostics"] = msg.get("diagnostics", [])
                done = True
            elif t == "fatal":
                fatal = msg.get("message")
        if timed_out or proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass
    pending = [r for r in results.values() if r["status"] == "not_run"]
    if (timed_out or fatal or not done) and pending:
        why = (f"harness timed out after {timeout or limits.check_timeout_s:.0f}s" if timed_out
               else f"harness failed: {fatal}" if fatal else "harness ended unexpectedly")
        pending[0].update(status="errored", message=why)
        for r in pending[1:]:
            r["message"] = "not run: " + why
        base["problems"].append(why)
    return _finish(base, results)


def _finish(report: dict, results: dict) -> dict:
    rs = list(results.values())
    counts = {k: sum(1 for r in rs if r["status"] == k) for k in ("passed", "failed", "errored", "not_run")}
    report["results"] = rs
    report["summary"] = counts
    report["all_passed"] = bool(rs) and counts["passed"] == len(rs)
    return report


def describe_conditions(report: dict) -> str:
    c = report.get("conditions", {})
    bits = [c.get("browser", "browser unknown")]
    if c.get("fixed_time"):
        bits.append(f"clock fixed at {c['fixed_time']}")
    bits.append("clean storage per criterion")
    bits.append("network limited to the app's own origin")
    return ", ".join(bits)


def compact_report(report: dict) -> dict:
    """What the model sees: outcomes and failure reasons, not the whole report."""
    return {
        "kind": report["kind"], "all_passed": report["all_passed"], "summary": report["summary"],
        "results": [{k: r[k] for k in ("id", "title", "status", "failed_step", "message", "role")
                     if r.get(k) not in (None, "")} for r in report["results"]],
        "problems": report.get("problems", [])[:5],
    }
