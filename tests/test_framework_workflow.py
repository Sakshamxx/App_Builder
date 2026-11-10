"""Workflow integration test for the FRAMEWORK implementation (APPBUILDER_IMPL=framework).

What is shared and what is graph-based (see COMPONENT_MAP.md):
  * SHARED service logic, identical in both implementations: scope assessment, criteria translation, revision judgment,
    the user-approval gates and their hashes, pending state, restore, export/import. A "pause for approval" is the service
    returning with ``runs/pending.json`` written and NO build running; the user's decision arrives as a later service call.
  * GRAPH-BASED, only in the framework implementation: the build/repair loop (nodes model / tools / no_tools, edges, caps).

This is a service-workflow test through the framework implementation. It is NOT a LangGraph interrupt or checkpoint test:
the graph is compiled without a checkpointer, never interrupts, and a pause is not a suspended graph run. Nothing here
shows that LangGraph persistence or interrupts would work, and none is used.
"""
import inspect
import json

from appbuilder.framework import graph
from appbuilder.testing.scripted import calls, js, write
from tests.fixtures_data import _add, criteria, read_app
from tests.helpers import OWNER, RUN, new_verified

JUDGE = {"verdict": "compatible", "explanation": "adds a count", "touched_requirement_ids": ["R2"],
         "added_requirements": [{"id": "R6", "text": "The dashboard shows how many expenses are in the current month."}],
         "conflicting_requirement_ids": [], "clarification_question": None}


def events(svc, pid):
    return [json.loads(x) for x in (svc.store.runs_dir(pid) / "trace.jsonl").read_text().splitlines()]


def test_revision_judgment_then_pause_then_approval_then_graph_build_with_a_repair(tmp_path):
    svc, pid = new_verified(tmp_path, "framework")
    base = events(svc, pid)
    assert any(e["type"] == "build_start" and e.get("impl") == "langgraph" for e in base)     # the initial build ran in the graph

    c2 = criteria("expense_tracker")["criteria"][1]["steps"][:-1]
    new_c = {"criteria": [
        {"id": "C1", "title": "Dashboard counts this month's expenses", "requirement_ids": ["R6"],
         "steps": c2 + [{"action": "assert_text", "target": "month-count", "equals": "2"}]},
        {"id": "C2", "title": "The count with different expenses", "requirement_ids": ["R6"],
         "steps": [{"action": "navigate", "page": "index.html"}] + _add("Food", "12", "2026-11-05") + _add("Food", "7", "2026-10-05")
                  + [{"action": "navigate", "page": "dashboard.html"}, {"action": "assert_text", "target": "month-count", "equals": "1"}]}],
        "unverifiable": []}
    dash = read_app("expense_tracker")["dashboard.html"]
    page = dash.replace("<p>", '<p data-testid="month-count" id="mc"></p><p>', 1)
    wrong = page.replace("</script>", "document.getElementById('mc').textContent = String(expenses.length);</script>")        # counts every month
    right = page.replace("</script>", "document.getElementById('mc').textContent = String(expenses.filter(e => e.date.slice(0, 7) === month).length);</script>")
    svc.test_transport.script += [js(JUDGE), js(new_c), calls(write("dashboard.html", wrong)), RUN, calls(write("dashboard.html", right)), RUN]

    # 1. revision judgment (shared service logic, one model call) -> criteria proposal -> PAUSE for the user
    st = svc.revise(OWNER, pid, "Show the number of expenses this month on the dashboard")
    mid = events(svc, pid)[len(base):]
    assert st["status"]["status"] == "awaiting_revision_approval" and st["revision"]["verdict"] == "compatible_needs_approval"
    assert any(e["type"] == "revision_judgment_end" and e["verdict"] == "compatible" for e in mid)
    assert not any(e["type"] in ("graph_node", "build_start") for e in mid), "no graph run exists while the service waits for the user"
    assert (svc.store.runs_dir(pid) / "pending.json").is_file() and svc.store.approved(pid)["approval"]["version"] == 1
    assert svc.test_transport.i == len(svc.test_transport.requests) == 6         # 4 (initial) + judgment + criteria; no build call yet

    # 2. the user's approval arrives as a later call; the service then continues into the framework build/repair loop
    st = svc.approve_revision(OWNER, pid, st["pending"]["criteria_hash"])
    after = events(svc, pid)[len(base) + len(mid):]
    nodes = [e["node"] for e in after if e["type"] == "graph_node"]
    assert any(e["type"] == "build_start" and e.get("impl") == "langgraph" and e["kind"] == "revision" for e in after)
    assert nodes.count("model") >= 3 and "tools" in nodes
    assert st["approved"]["approval"]["version"] == 2 and st["approved"]["approval"]["source"] == "user_approval_revision"
    o = st["outcome"]
    assert o["status"] == "verified" and o["repairs"] == 1 and o["stop_reason"] == "checks_passed", o      # failed check -> repair -> pass
    assert st["revision"]["roles"]["C10"] == "relevant" and st["revision"]["roles"]["C1"] == "regression"
    assert st["pending"] is None and st["status"]["status"] == "verified"


def test_the_framework_build_has_no_graph_persistence_or_interrupts():
    src = inspect.getsource(graph)
    for word in ("checkpointer", "interrupt(", "Command(", "MemorySaver", "SqliteSaver", "thread_id"):
        assert word not in src.replace("`", ""), f"{word} would make this a persistence/interrupt design; it is intentionally not used"
