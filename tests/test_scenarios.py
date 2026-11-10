"""End-to-end scenarios for BOTH implementations (handwritten loop and LangGraph) on a scripted stub model.

Evidence level: these runs prove our code's behaviour (stop rules, status, approvals, restore ...) when the model behaves as scripted.
They say nothing about how any real model behaves. Live-model checks are separate (see tests/test_live.py).
"""
import json

import pytest

from appbuilder.agent.service import Busy, UserError
from appbuilder.config import Limits
from appbuilder.supplied.model_transport import TransportError
from appbuilder.supplied.store import read_json
from appbuilder.testing.scripted import calls, js, text, write
from tests.fixtures_data import EXPENSE_BUG, _add, criteria, read_app, scope
from tests.helpers import (OWNER, OWNER2, RUN, criteria_reply, drive_to_build, make_service, new_verified, scope_reply, write_all)

IMPLS = ["handwritten", "framework"]
pytestmark = pytest.mark.parametrize("impl", IMPLS)


def events(svc, pid):
    p = svc.store.runs_dir(pid) / "trace.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()]


# ------------------------------------------------------------------ representative one-, two- and three-page apps
@pytest.mark.parametrize("name,pages", [("tip_calculator", 1), ("habit_tracker", 2), ("expense_tracker", 3)])
def test_representative_apps_are_built_and_verified(tmp_path, impl, name, pages):
    script = [scope_reply(name), criteria_reply(name), write_all(name), RUN]
    svc = make_service(tmp_path, impl, script)
    st = drive_to_build(svc)
    assert st["status"]["status"] == "verified" and st["status"]["wording"] == "passed the listed checks under these conditions"
    assert len(st["approved"]["scope"]["pages"]) == pages and st["restore_point"]
    assert st["outcome"]["stop_reason"] == "checks_passed" and st["outcome"]["repairs"] == 0
    assert st["status"]["checks"]["passed"] == st["status"]["checks"]["total"]
    u = st["usage"]
    assert u["model_calls"] == 4 and u["prompt_tokens"] == 100 + 100 + 200 + 200 and u["cost"]["status"] == "unpriced"


def test_streaming_and_trace_record_the_run(tmp_path, impl):
    seen = []
    script = [scope_reply("tip_calculator"), criteria_reply("tip_calculator"), write_all("tip_calculator"), RUN]
    svc = make_service(tmp_path, impl, script)
    st = svc.create(OWNER, "tip calculator")
    svc.sinks[st["id"]] = [seen.append]
    st = svc.approve_scope(OWNER, st["id"], st["pending"]["scope_hash"])
    svc.approve_criteria(OWNER, st["id"], st["pending"]["criteria_hash"])
    types = [e["type"] for e in seen]
    assert "model_text" in types and "model_call_end" in types and "tool_call" in types and "check_report" in types and "build_end" in types
    assert types.index("build_start") < types.index("check_report") < types.index("build_end")
    tr = events(svc, st["id"])
    assert any(e["type"] == "tool_call" and e["name"] == "write_file" for e in tr)
    if impl == "framework":
        assert {"model", "tools"} <= {e["node"] for e in tr if e["type"] == "graph_node"}


# ------------------------------------------------------------------ failed check, then repair
def test_failed_check_then_repair_succeeds(tmp_path, impl):
    fixed = read_app("expense_tracker")["dashboard.html"]
    script = [scope_reply("expense_tracker"), criteria_reply("expense_tracker"), write_all("expense_tracker", EXPENSE_BUG), RUN,
              calls(write("dashboard.html", fixed)), RUN]
    svc = make_service(tmp_path, impl, script)
    st = drive_to_build(svc)
    o = st["outcome"]
    assert o["status"] == "verified" and o["repairs"] == 1 and o["stop_reason"] == "checks_passed"
    tr = events(svc, st["id"])
    reports = [e for e in tr if e["type"] == "check_report"]
    assert [r["all_passed"] for r in reports] == [False, True] and set(reports[0]["failed"]) == {"C2", "C4", "C7", "C9"}
    assert [e["repair"] for e in tr if e["type"] == "repair_started"] == [1]
    # the failure message reached the model: the second build request contains the failing criterion text
    sent = svc.test_transport.requests[-2]["messages"]
    assert any("95.00" in m.get("content", "") for m in sent if m["role"] == "tool")


# ------------------------------------------------------------------ cap reached -> incomplete + explicit failure report
def test_repair_cap_reached_is_incomplete_with_explicit_report(tmp_path, impl):
    bad = write_all("expense_tracker", EXPENSE_BUG)
    tweak = lambda n: calls(write("dashboard.html", read_app("expense_tracker", EXPENSE_BUG)["dashboard.html"] + f"<!-- try {n} -->"))
    script = [scope_reply("expense_tracker"), criteria_reply("expense_tracker"), bad, RUN, tweak(1), RUN, tweak(2), RUN, tweak(3), RUN]
    svc = make_service(tmp_path, impl, script)
    st = drive_to_build(svc)
    o = st["outcome"]
    assert o["status"] == "incomplete" and o["stop_reason"] == "repair_cap" and o["repairs"] == 3
    f = o["failure"]
    assert "3 repair attempts" in f["headline"] and {c["id"] for c in f["failing_checks"]} == {"C2", "C4", "C7", "C9"}
    assert "NOT verified" in f["status_note"] and f["tried"]["repairs"] == 3 and f["limits"]["max_repairs"] == 3
    assert st["status"]["status"] == "incomplete" and st["restore_point"] is None
    assert svc.test_transport.i == len(script)                       # no model call after the cap


def test_step_cap_is_reported_as_incomplete(tmp_path, impl):
    script = [scope_reply("tip_calculator"), criteria_reply("tip_calculator"), calls(("list_files", {}))]
    svc = make_service(tmp_path, impl, script, loop_last=True)
    st = drive_to_build(svc)
    o = st["outcome"]
    assert o["status"] == "incomplete" and o["stop_reason"] == "step_cap" and o["turns"] == 20
    assert "step limit (20 of 20" in o["failure"]["headline"] and st["status"]["status"] == "incomplete"
    assert sum(1 for r in svc.test_transport.requests if r["tools"]) == 20      # build calls: exactly the cap


def test_extra_tool_calls_in_one_turn_are_refused_as_data(tmp_path, impl):
    many = calls(*[("list_files", {})] * 8)
    script = [scope_reply("tip_calculator"), criteria_reply("tip_calculator"), many, write_all("tip_calculator"), RUN]
    svc = make_service(tmp_path, impl, script)
    st = drive_to_build(svc)
    tool_msgs = [m for m in svc.test_transport.requests[3]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 8 and sum("was not run" in m["content"] for m in tool_msgs) == 2
    assert st["status"]["status"] == "verified"


# ------------------------------------------------------------------ the model's word is not evidence
def test_model_claiming_success_without_a_check_is_checked_by_the_loop(tmp_path, impl):
    fixed = read_app("expense_tracker")["dashboard.html"]
    script = [scope_reply("expense_tracker"), criteria_reply("expense_tracker"), write_all("expense_tracker", EXPENSE_BUG),
              text("All done, everything works!"),                    # no run_checks: the loop must run it
              calls(write("dashboard.html", fixed)), RUN]
    svc = make_service(tmp_path, impl, script)
    st = drive_to_build(svc)
    tr = events(svc, st["id"])
    assert any(e["type"] == "auto_check" for e in tr)
    assert st["outcome"]["status"] == "verified" and st["outcome"]["repairs"] == 1


def test_model_that_gives_up_with_failing_checks_is_not_verified(tmp_path, impl):
    script = [scope_reply("expense_tracker"), criteria_reply("expense_tracker"), write_all("expense_tracker", EXPENSE_BUG), RUN,
              text("I could not fix it.")]
    svc = make_service(tmp_path, impl, script)
    st = drive_to_build(svc)
    assert st["outcome"]["stop_reason"] == "model_stopped" and st["status"]["status"] == "incomplete"


def test_diagnostics_never_count_as_acceptance(tmp_path, impl):
    diag = calls(("run_diagnostic", {"title": "peek", "steps": [{"action": "navigate", "page": "index.html"},
                                                                {"action": "assert_visible", "target": "calculate"}]}))
    script = [scope_reply("tip_calculator"), criteria_reply("tip_calculator"), write_all("tip_calculator"), diag,
              text("done"), RUN]
    svc = make_service(tmp_path, impl, script)
    st = drive_to_build(svc)
    tr = events(svc, st["id"])
    kinds = [(e["kind"], e["all_passed"]) for e in tr if e["type"] == "check_report"]
    assert kinds == [("diagnostic", True), ("acceptance", True)]       # the diagnostic passed but the loop still ran the real check
    assert any(e["type"] == "auto_check" for e in tr)
    diag_result = [m for m in svc.test_transport.requests[4]["messages"] if m["role"] == "tool"][-1]
    assert "DIAGNOSTIC ONLY" in diag_result["content"]


def test_diagnostic_limit_and_invalid_steps_are_reported_as_data(tmp_path, impl):
    bad = calls(("run_diagnostic", {"steps": [{"action": "run_shell"}]}))
    script = [scope_reply("tip_calculator"), criteria_reply("tip_calculator"), write_all("tip_calculator"), bad, RUN]
    svc = make_service(tmp_path, impl, script)
    drive_to_build(svc)
    res = json.loads([m for m in svc.test_transport.requests[4]["messages"] if m["role"] == "tool"][-1]["content"])
    assert res["ok"] is False and "not valid" in res["error"]


def test_agent_file_tools_cannot_reach_or_edit_the_approved_criteria(tmp_path, impl):
    attack = calls(write("../approved/criteria.json", "{}"), ("read_file", {"path": "../approved/criteria.json"}),
                   ("read_file", {"path": "approved.json"}), write("criteria.json", "{}"), ("write_file", {"path": "x.py", "content": "import os"}),
                   ("nope", {}))
    script = [scope_reply("tip_calculator"), criteria_reply("tip_calculator"), attack, write_all("tip_calculator"), RUN]
    svc = make_service(tmp_path, impl, script)
    pre = None
    st = svc.create(OWNER, "tip")
    st = svc.approve_scope(OWNER, st["id"], st["pending"]["scope_hash"])
    pid, ch = st["id"], st["pending"]["criteria_hash"]
    st = svc.approve_criteria(OWNER, pid, ch)
    results = [json.loads(m["content"]) for m in svc.test_transport.requests[3]["messages"] if m["role"] == "tool"]
    assert len(results) == 6 and not any(r["ok"] for r in results)
    assert svc.store.approved(pid)["approval"]["criteria_hash"] == ch and svc.store.approved(pid)["criteria"] == criteria("tip_calculator")
    assert st["status"]["status"] == "verified"


# ------------------------------------------------------------------ transient failures vs repairs
def test_transient_errors_are_retried_with_backoff_and_not_counted_as_repairs(tmp_path, impl):
    rl = TransportError("rate_limit", "slow down", status=429, retry_after=3.0)
    script = [scope_reply("tip_calculator"), criteria_reply("tip_calculator"), rl, TransportError("timeout", "t"), write_all("tip_calculator"), RUN]
    svc = make_service(tmp_path, impl, script)
    st = drive_to_build(svc)
    assert st["outcome"]["repairs"] == 0 and st["outcome"]["status"] == "verified"
    assert svc.test_sleeps == [3.0, 2.0]                                    # retry_after honoured, then exponential backoff
    assert [e["type"] for e in events(svc, st["id"]) if e["type"].startswith("transport")] == ["transport_retry"] * 2


def test_retries_exhausted_stops_with_model_unavailable(tmp_path, impl):
    script = [scope_reply("tip_calculator"), criteria_reply("tip_calculator")] + [TransportError("server", "503", status=503)] * 3
    svc = make_service(tmp_path, impl, script)
    st = drive_to_build(svc)
    o = st["outcome"]
    assert o["stop_reason"] == "model_unavailable" and o["repairs"] == 0 and o["status"] == "incomplete"
    assert "could not be reached" in o["failure"]["headline"] and st["status"]["status"] == "incomplete"


def test_auth_error_is_not_retried(tmp_path, impl):
    script = [scope_reply("tip_calculator"), criteria_reply("tip_calculator"), TransportError("auth", "bad key", status=401)]
    svc = make_service(tmp_path, impl, script)
    st = drive_to_build(svc)
    assert st["outcome"]["stop_reason"] == "model_error" and st["outcome"]["status"] == "error" and svc.test_sleeps == []


# ------------------------------------------------------------------ scope assessment, unsupported requests and criteria
def test_unsupported_request_is_explained_and_an_alternative_needs_approval(tmp_path, impl):
    unsup = {"supported": False, "app_title": "Shop", "summary": "x", "pages": [], "requirements": [],
             "unsupported": [{"feature": "online payments", "reason": "no payments or backend are available"},
                             {"feature": "user accounts", "reason": "no accounts in a browser-only app"}],
             "proposed_alternative": "A product catalogue with a cart total that stays in the browser; no checkout."}
    alt = {**scope("tip_calculator"), "app_title": "Catalogue"}
    script = [js(unsup), js(alt)]
    svc = make_service(tmp_path, impl, script)
    st = svc.create(OWNER, "online shop with payments and logins")
    assert st["status"]["status"] == "unsupported" and "online payments" in st["pending"]["readable"]
    assert svc.store.approved(st["id"]) is None
    with pytest.raises(UserError):
        svc.approve_scope(OWNER, st["id"], "x")                          # nothing can be approved for an unsupported request
    st = svc.accept_alternative(OWNER, st["id"])
    assert st["status"]["status"] == "awaiting_scope_approval" and st["pending"]["scope"]["app_title"] == "Catalogue"


def test_partly_unsupported_request_shows_what_is_dropped_and_unverifiable_criteria(tmp_path, impl):
    partial = {**scope("tip_calculator"), "unsupported": [{"feature": "emailing receipts", "reason": "no email from a browser-only app"}],
               "proposed_alternative": "Show a printable summary instead."}
    unv = [{"requirement_id": "R2", "reason": "persistence across browser restarts cannot be simulated"}]
    c = criteria("tip_calculator"); c["criteria"] = [c["criteria"][0], c["criteria"][2]]       # R1 with two examples
    script = [js(partial), js({**c, "unverifiable": unv})]
    svc = make_service(tmp_path, impl, script)
    st = svc.create(OWNER, "tip calculator that emails receipts")
    assert "NOT included" in st["pending"]["readable"] and "emailing receipts" in st["pending"]["readable"]
    st = svc.approve_scope(OWNER, st["id"], st["pending"]["scope_hash"])
    assert "cannot be checked automatically" in st["pending"]["readable"] and "R2" in st["pending"]["readable"]
    assert st["pending"]["unverifiable"][0]["requirement_id"] == "R2"


def test_criteria_outside_the_vocabulary_are_sent_back_to_the_model(tmp_path, impl):
    badc = criteria("tip_calculator"); badc["criteria"][0]["steps"].append({"action": "assert_network_request", "url": "x"})
    script = [scope_reply("tip_calculator"), js({**badc, "unverifiable": []}), criteria_reply("tip_calculator")]
    svc = make_service(tmp_path, impl, script)
    st = svc.create(OWNER, "tip")
    st = svc.approve_scope(OWNER, st["id"], st["pending"]["scope_hash"])
    assert st["pending"]["criteria"] == criteria("tip_calculator")
    feedback = svc.test_transport.requests[2]["messages"][-1]["content"]
    assert "assert_network_request" in feedback and "not a supported" in feedback


def test_requirement_without_a_criterion_is_rejected_until_covered_or_declared_unverifiable(tmp_path, impl):
    c = criteria("tip_calculator"); c["criteria"] = [c["criteria"][0], c["criteria"][2]]       # R1 only
    script = [scope_reply("tip_calculator"), js({**c, "unverifiable": []}), criteria_reply("tip_calculator")]
    svc = make_service(tmp_path, impl, script)
    st = svc.create(OWNER, "tip")
    st = svc.approve_scope(OWNER, st["id"], st["pending"]["scope_hash"])
    assert "R2" in svc.test_transport.requests[2]["messages"][-1]["content"] and len(st["pending"]["criteria"]["criteria"]) == 4


def test_structured_output_retry_then_clear_failure(tmp_path, impl):
    svc = make_service(tmp_path, impl, [text("sure! here you go"), scope_reply("tip_calculator")])
    st = svc.create(OWNER, "tip")
    assert st["status"]["status"] == "awaiting_scope_approval"
    svc2 = make_service(tmp_path / "b", impl, [text("nope")] * 3)
    with pytest.raises(UserError, match="could not produce a valid answer"):
        svc2.create(OWNER, "tip")


def test_approvals_are_bound_to_what_the_user_saw(tmp_path, impl):
    svc = make_service(tmp_path, impl, [scope_reply("tip_calculator"), criteria_reply("tip_calculator")])
    st = svc.create(OWNER, "tip")
    with pytest.raises(UserError, match="changed since you reviewed"):
        svc.approve_scope(OWNER, st["id"], "0" * 64)
    st = svc.approve_scope(OWNER, st["id"], st["pending"]["scope_hash"])
    with pytest.raises(UserError, match="changed since you reviewed"):
        svc.approve_criteria(OWNER, st["id"], "0" * 64)
    assert svc.store.approved(st["id"]) is None


def test_projects_are_scoped_to_their_owner(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl, "tip_calculator")
    from appbuilder.supplied.store import StoreError
    with pytest.raises(StoreError):
        svc.state(OWNER2, pid)
    assert svc.list(OWNER2) == [] and len(svc.list(OWNER)) == 1


# ------------------------------------------------------------------ revisions
JUDGE_OK = {"verdict": "compatible", "explanation": "cosmetic", "touched_requirement_ids": ["R1"], "added_requirements": [],
            "conflicting_requirement_ids": [], "clarification_question": None}


def test_compatible_revision_runs_all_checks_and_replaces_the_restore_point(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    before = svc.state(OWNER, pid)["restore_point"]["files_hash"]
    idx = read_app("expense_tracker")["index.html"].replace("Add expense</button>", "Save expense</button>")
    svc.test_transport.script += [js(JUDGE_OK), calls(write("index.html", idx)), RUN]
    st = svc.revise(OWNER, pid, "Rename the add button to 'Save expense'")
    assert st["outcome"]["status"] == "verified" and st["revision"]["roles"]["C1"] == "relevant" and st["revision"]["roles"]["C2"] == "regression"
    assert st["restore_point"]["files_hash"] != before
    assert st["status"]["checks"]["total"] == 9                                   # every approved criterion was re-run


def test_revision_that_breaks_a_regression_is_caught(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    broken = read_app("expense_tracker")["dashboard.html"].replace("e.date.slice(0, 7) === month", "true")
    svc.test_transport.script += [js(JUDGE_OK), calls(write("dashboard.html", broken)), RUN,
                                  text("I changed it")]                         # gives up with failing checks
    st = svc.revise(OWNER, pid, "Rename the add button")
    reports = [e for e in events(svc, pid) if e["type"] == "check_report"]
    assert reports[-1]["all_passed"] is False and set(reports[-1]["failed"]) == {"C2", "C4", "C7", "C9"}
    assert st["outcome"]["status"] == "incomplete" and st["status"]["status"] == "incomplete"


def test_failed_revision_can_be_restored_to_the_last_working_version(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    good = svc.state(OWNER, pid)["restore_point"]["files_hash"]
    broken = read_app("expense_tracker")["dashboard.html"].replace("e.date.slice(0, 7) === month", "true")
    svc.test_transport.script += [js(JUDGE_OK), calls(write("dashboard.html", broken)), RUN, text("done")]
    st = svc.revise(OWNER, pid, "tweak")
    assert st["status"]["status"] == "incomplete"
    st = svc.restore_last_working(OWNER, pid)
    assert st["status"]["status"] == "verified"
    from appbuilder.supplied.workspace import Workspace
    assert Workspace(svc.store.app_dir(pid), svc.settings.limits).files_hash() == good


def test_conflicting_revision_stops_and_asks_without_touching_files(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    from appbuilder.supplied.workspace import Workspace
    h = Workspace(svc.store.app_dir(pid), svc.settings.limits).files_hash()
    conflict = {"verdict": "conflict", "explanation": "R2 says this month", "touched_requirement_ids": [], "added_requirements": [],
                "conflicting_requirement_ids": ["R2"], "clarification_question": "R2 says the dashboard total is for the current month. Do you want weekly instead of monthly?"}
    svc.test_transport.script += [js(conflict)]
    n = svc.test_transport.i
    st = svc.revise(OWNER, pid, "Make the dashboard weekly instead of monthly")
    assert st["status"]["status"] == "needs_clarification" and "weekly instead of monthly" in st["pending"]["question"]
    assert st["revision"]["verdict"] == "conflict" and svc.test_transport.i == n + 1      # one judgment call, no build
    assert Workspace(svc.store.app_dir(pid), svc.settings.limits).files_hash() == h
    assert svc.store.approved(pid)["scope"] == scope("expense_tracker")                 # approved contract untouched
    st = svc.resolve_clarification(OWNER, pid, "keep")
    assert st["status"]["status"] == "verified" and st["pending"] is None


def test_conflict_resolved_by_replacing_the_requirement_needs_reapproval_then_rebuilds(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    conflict = {"verdict": "conflict", "explanation": "x", "touched_requirement_ids": [], "added_requirements": [],
                "conflicting_requirement_ids": ["R2"], "clarification_question": "Replace monthly with weekly?"}
    new_scope = scope("expense_tracker")
    new_scope["requirements"][1]["text"] = "The dashboard shows the total of expenses dated in the current week (Monday to Sunday) only."
    new_crit = criteria("expense_tracker")
    # clock fixed at Sunday 2026-11-15, so the week is Mon 9 Nov - Sun 15 Nov: Food 25 (9th), Travel 40 (12th), Food 30 (1st, last week)
    remap = {"2026-11-03": "2026-11-09", "2026-11-10": "2026-11-12", "2026-10-28": "2026-11-01"}
    for c in new_crit["criteria"]:
        for st_ in c["steps"]:
            if st_.get("action") == "enter" and st_["target"] == "date":
                st_["value"] = remap.get(st_["value"], st_["value"])
    new_crit["criteria"][1]["title"] = "The dashboard total counts only this week"
    dash = read_app("expense_tracker")["dashboard.html"].replace(
        "const total = expenses.filter(e => e.date.slice(0, 7) === month)",
        "const dow = (now.getDay() + 6) % 7, iso = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');\n"
        "const mon = new Date(now.getFullYear(), now.getMonth(), now.getDate() - dow), sun = new Date(now.getFullYear(), now.getMonth(), now.getDate() - dow + 6);\n"
        "const total = expenses.filter(e => e.date >= iso(mon) && e.date <= iso(sun))")
    svc.test_transport.script += [js(conflict), js(new_scope), js({**new_crit, "unverifiable": []}), calls(write("dashboard.html", dash)), RUN]
    st = svc.revise(OWNER, pid, "Make the dashboard weekly instead of monthly")
    st = svc.resolve_clarification(OWNER, pid, "replace")
    assert st["status"]["status"] == "awaiting_revision_approval" and st["pending"]["touched"] == ["R2"]
    assert svc.store.approved(pid)["scope"]["requirements"][1]["text"].endswith("current month only.")   # not changed before approval
    with pytest.raises(UserError):
        svc.approve_revision(OWNER, pid, "0" * 64)
    st = svc.approve_revision(OWNER, pid, st["pending"]["criteria_hash"])
    assert st["approved"]["approval"]["version"] == 2 and "week" in st["approved"]["scope"]["requirements"][1]["text"]
    assert st["outcome"]["status"] == "verified", st["outcome"]["failure"]
    assert st["revision"]["roles"]["C2"] == "relevant" and st["revision"]["roles"]["C3"] == "regression"


def test_compatible_revision_that_adds_a_requirement_needs_new_criteria_approval(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    judge = {**JUDGE_OK, "touched_requirement_ids": ["R2"], "added_requirements": [{"id": "R6", "text": "The dashboard shows how many expenses are in the current month."}]}
    new_c = {"criteria": [{"id": "C1", "title": "Dashboard counts this month's expenses", "requirement_ids": ["R6"],
                           "steps": criteria("expense_tracker")["criteria"][1]["steps"][:-1] + [
                               {"action": "assert_text", "target": "month-count", "equals": "2"}]},
                          {"id": "C2", "title": "The count with different expenses", "requirement_ids": ["R6"],
                           "steps": [{"action": "navigate", "page": "index.html"}] + _add("Food", "12", "2026-11-05") + _add("Food", "7", "2026-10-05")
                                    + [{"action": "navigate", "page": "dashboard.html"}, {"action": "assert_text", "target": "month-count", "equals": "1"}]}],
             "unverifiable": []}
    dash = read_app("expense_tracker")["dashboard.html"]
    assert "month-total" in dash
    dash2 = dash.replace('<p>', '<p data-testid="month-count" id="mc"></p><p>', 1)
    dash2 = dash2.replace("</script>", "document.getElementById('mc').textContent = String(expenses.filter(e => e.date.slice(0, 7) === month).length);</script>")
    svc.test_transport.script += [js(judge), js(new_c), calls(write("dashboard.html", dash2)), RUN]
    st = svc.revise(OWNER, pid, "Show the number of expenses this month on the dashboard")
    assert st["status"]["status"] == "awaiting_revision_approval" and st["revision"]["verdict"] == "compatible_needs_approval"
    assert len(st["pending"]["criteria"]["criteria"]) == 11 and st["pending"]["criteria"]["criteria"][10]["id"] == "C11"
    assert len(svc.store.approved(pid)["criteria"]["criteria"]) == 9                      # not approved until the user says so
    st = svc.approve_revision(OWNER, pid, st["pending"]["criteria_hash"])
    assert st["approved"]["approval"]["version"] == 2 and len(st["approved"]["criteria"]["criteria"]) == 11
    assert st["outcome"]["status"] == "verified", st["outcome"]["failure"]
    assert st["revision"]["roles"]["C10"] == "relevant" and st["revision"]["roles"]["C1"] == "regression"


def test_unsupported_revision_is_declined_without_changes(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    svc.test_transport.script += [js({"verdict": "unsupported", "explanation": "syncing between devices needs a server",
                                      "touched_requirement_ids": [], "added_requirements": [], "conflicting_requirement_ids": [], "clarification_question": None})]
    st = svc.revise(OWNER, pid, "Sync my expenses across my phone and laptop")
    assert st["revision"]["verdict"] == "unsupported" and "server" in st["pending"]["readable"] and st["status"]["status"] == "verified"


# ------------------------------------------------------------------ export / import / restore through the service
def test_export_import_requires_reapproval_and_reruns_checks_without_a_model(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    data = svc.export(OWNER, pid)
    svc.test_transport.script = []                                   # any further model call would raise
    calls_before = svc.test_transport.i
    st = svc.import_archive(OWNER2, data)
    nid = st["id"]
    assert st["import"]["status"] == "imported_untrusted" and st["status"]["status"] == "awaiting_criteria_approval"
    assert "NOT TRUSTED" in st["pending"]["readable"] and svc.store.approved(nid) is None
    st = svc.approve_criteria(OWNER2, nid, st["pending"]["criteria_hash"])
    assert st["recheck"]["all_passed"] and st["status"]["status"] == "verified" and st["restore_point"]
    assert svc.test_transport.i == calls_before                      # the re-check used the harness, not the model


def test_imported_app_that_fails_its_checks_is_not_verified(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    (svc.store.app_dir(pid) / "dashboard.html").write_text(read_app("expense_tracker", EXPENSE_BUG)["dashboard.html"])
    data = svc.export(OWNER, pid)                                    # tampered files travel with claims of "verified" criteria
    st = svc.import_archive(OWNER2, data)
    st = svc.approve_criteria(OWNER2, st["id"], st["pending"]["criteria_hash"])
    assert st["recheck"]["all_passed"] is False and st["status"]["status"] != "verified" and st["restore_point"] is None


def test_restore_without_restore_point_and_after_a_save(tmp_path, impl):
    svc = make_service(tmp_path, impl, [scope_reply("tip_calculator")])
    st = svc.create(OWNER, "tip")
    with pytest.raises(UserError, match="no restore point"):
        svc.restore_last_working(OWNER, st["id"])


# ------------------------------------------------------------------ secrets
def test_secrets_never_reach_trace_usage_exports_or_state(tmp_path, impl):
    key = "AIzaSyCANARYcanaryCANARY0123456789"
    leaky = text("the key is " + key, usage=(1, 1))
    script = [scope_reply("tip_calculator"), criteria_reply("tip_calculator"),
              calls(write("index.html", "<p>x</p>"), content="my key is " + key), RUN, leaky]
    svc = make_service(tmp_path, impl, script, api_key=key)
    st = drive_to_build(svc)
    blob = ""
    for p in svc.store.dir(st["id"]).rglob("*"):
        if p.is_file() and "app" not in p.parts:
            blob += p.read_text(errors="ignore")
    blob += json.dumps(st)
    assert key not in blob and "CANARYcanary" not in blob
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(svc.export(OWNER, st["id"])))
    assert all(key.encode() not in z.read(n) for n in z.namelist())


def test_concurrent_operations_on_one_project_are_refused(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl, "tip_calculator")
    lock = svc._locked(pid)
    try:
        with pytest.raises(Busy):
            svc.build(OWNER, pid)
    finally:
        lock.release()
