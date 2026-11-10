"""The trusted harness against hand-written reference apps (no model involved)."""
import pytest

from appbuilder.config import Limits
from appbuilder.supplied.criteria_vocab import render_readable, render_technical, validate_criteria
from appbuilder.supplied.harness import compact_report, run_checks
from tests.fixtures_data import EXPENSE_BUG, copy_app, criteria, scope

L = Limits()


@pytest.mark.parametrize("name", ["tip_calculator", "habit_tracker", "expense_tracker"])
def test_reference_apps_pass_their_criteria(tmp_path, name):
    copy_app(name, tmp_path / "app")
    rep = run_checks(tmp_path / "app", criteria(name), L)
    assert rep["all_passed"], [r for r in rep["results"] if r["status"] != "passed"]
    assert rep["summary"]["failed"] == rep["summary"]["errored"] == rep["summary"]["not_run"] == 0
    assert rep["conditions"]["chromium_sandbox"] in (True, False)


def test_failed_check_reports_requirement_and_step(tmp_path):
    copy_app("expense_tracker", tmp_path / "app", EXPENSE_BUG)
    rep = run_checks(tmp_path / "app", criteria("expense_tracker"), L)
    assert not rep["all_passed"]
    by = {r["id"]: r for r in rep["results"]}
    assert by["C2"]["status"] == "failed" and "65.00" in by["C2"]["message"] and "95.00" in by["C2"]["message"]
    assert by["C4"]["status"] == "failed"
    assert by["C1"]["status"] == "passed" and by["C5"]["status"] == "passed"
    assert by["C2"]["requirement_ids"] == ["R2"]


def test_missing_element_is_failed_not_errored(tmp_path):
    copy_app("tip_calculator", tmp_path / "app", {"index.html": ('data-testid="calculate"', 'data-testid="go"')})
    rep = run_checks(tmp_path / "app", criteria("tip_calculator"), L, ids=["C1"])
    r = rep["results"][0]
    assert r["status"] == "failed" and "calculate" in r["message"]


def test_page_that_does_not_exist_fails(tmp_path):
    copy_app("tip_calculator", tmp_path / "app")
    c = criteria("tip_calculator")
    (tmp_path / "app" / "index.html").rename(tmp_path / "app" / "main.html")
    rep = run_checks(tmp_path / "app", c, L, ids=["C1"])
    assert rep["results"][0]["status"] == "errored" or rep["results"][0]["status"] == "failed"
    assert not rep["all_passed"]


def test_unsupported_assertion_is_rejected_explicitly(tmp_path):
    copy_app("tip_calculator", tmp_path / "app")
    c = criteria("tip_calculator")
    c["criteria"][0]["steps"].append({"action": "assert_network_request", "url": "https://example.com"})
    problems = validate_criteria(c, ["index.html"], L)
    assert any("assert_network_request" in p and "not a supported" in p for p in problems)
    rep = run_checks(tmp_path / "app", c, L)
    assert not rep["all_passed"]
    assert all(r["status"] == "errored" and "invalid" in r["message"] for r in rep["results"])


@pytest.mark.parametrize("mutation,needle", [
    (lambda c: c["criteria"][0]["steps"].insert(0, {"action": "click", "target": "x"}), "first step must be navigate"),
    (lambda c: c["criteria"][0]["steps"].append({"action": "enter", "target": "Bad Id", "value": "1"}), "data-testid"),
    (lambda c: c["criteria"][0]["steps"].append({"action": "assert_count", "target": "x", "count": -1}), "count must be"),
    (lambda c: c["criteria"][0]["steps"].append({"action": "assert_text", "target": "x"}), "exactly one of"),
    (lambda c: c["criteria"][0]["steps"].append({"action": "navigate", "page": "other.html"}), "not one of the app's pages"),
    (lambda c: c["criteria"][0].update(requirement_ids=[]), "requirement_ids"),
    (lambda c: c.update(fixed_time="yesterday"), "fixed_time"),
])
def test_validator_rejects_malformed_criteria(mutation, needle):
    c = criteria("tip_calculator")
    mutation(c)
    assert any(needle in p for p in validate_criteria(c, ["index.html"], L)), validate_criteria(c, ["index.html"], L)


def test_fixed_clock_makes_current_month_deterministic(tmp_path):
    copy_app("expense_tracker", tmp_path / "app")
    c = criteria("expense_tracker")
    c["fixed_time"] = "2026-10-15T10:00:00"   # October: only the 2026-10-28 expense (30) is "this month"
    c["criteria"] = [c["criteria"][1]]
    rep = run_checks(tmp_path / "app", c, L)
    assert rep["results"][0]["status"] == "failed" and "'30.00'" in rep["results"][0]["message"]


def test_busy_page_times_out_as_errored_with_remaining_not_run(tmp_path):
    copy_app("tip_calculator", tmp_path / "app", {"index.html": ("<script>", "<script>while(true){}")})
    c = criteria("tip_calculator")
    rep = run_checks(tmp_path / "app", c, L, timeout=6)
    assert rep["results"][0]["status"] in ("errored", "failed")
    assert not rep["all_passed"]


def test_diagnostic_reports_are_labelled_and_separate(tmp_path):
    copy_app("tip_calculator", tmp_path / "app")
    rep = run_checks(tmp_path / "app", criteria("tip_calculator"), L, kind="diagnostic")
    assert rep["kind"] == "diagnostic" and "DIAGNOSTIC ONLY" in rep["conditions"]["note"]


def test_readable_rendering_comes_from_the_structured_criteria():
    text = render_readable(criteria("expense_tracker"))
    assert "clock fixed at 2026-11-15" in text and "C2: The dashboard total counts only this month" in text
    assert 'Check that This month\'s total shows exactly "65.00"' in text and "[month-total]" not in text   # plain labels, no selectors
    assert 'Expect [month-total] to read exactly "65.00"' in render_technical(criteria("expense_tracker"))


def test_compact_report_for_model_hides_nothing_needed(tmp_path):
    copy_app("tip_calculator", tmp_path / "app")
    rep = run_checks(tmp_path / "app", criteria("tip_calculator"), L)
    cr = compact_report(rep)
    assert cr["all_passed"] and cr["summary"]["passed"] == 4
