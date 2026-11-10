"""Behavioural acceptance: what users approve, and what a hardcoded app can and cannot pass.

Evidence level: real browser checks (Playwright/Chromium) against hand-written fixture apps, plus pure unit tests of the criteria
rules. No model is involved. These show the harness and the proposal-time rules behave as described; they do NOT show that test
gaming is impossible (see ``test_listed_example_lookup_passes_*`` and DECISIONS.md).
"""
import re

import pytest

from appbuilder.agent.contracts import CriteriaProposal
from appbuilder.agent.criteria import check_criteria, variation_problems
from appbuilder.config import Limits
from appbuilder.supplied.criteria_vocab import render_readable, render_technical, validate_criteria
from appbuilder.supplied.harness import run_checks
from tests.fixtures_data import (APPS, TIP_SCOPE, copy_adversarial, copy_app, criteria, scope, single_example)

L = Limits()


def run(tmp_path, crit):
    return run_checks(tmp_path / "app", crit, L)


def failed_ids(rep):
    return sorted(r["id"] for r in rep["results"] if r["status"] != "passed")


# ---------------------------------------------------------------- readable criteria: same structure, no selectors
@pytest.mark.parametrize("name", list(APPS))
def test_readable_text_shows_behaviour_and_no_selectors(name):
    crit, sc = criteria(name), scope(name)
    text = render_readable(crit, sc)
    testids = {s["target"] for c in crit["criteria"] for s in c["steps"] if "target" in s}
    steps = "\n".join(l for l in text.splitlines() if re.match(r"^\s+\d+\. ", l))     # requirement text may use the same plain words
    for t in testids:
        # a one-word testid ("budget") is also a plain word, so it is only checked in selector form; hyphenated ones are checked anywhere in a step
        assert f"[{t}]" not in text and f'"{t}"' not in text and not (("-" in t or "_" in t) and t in steps), f"selector {t!r} leaked"
    assert "[" not in text and "data-testid" not in text
    for r in sc["requirements"]:
        assert r["text"] in text                              # each check is shown under the requirement it covers
    tech = render_technical(crit)
    assert all(f"[{t}]" in tech for t in testids)             # the builder still gets the testids
    assert tech != text


def test_readable_and_technical_come_from_the_same_structured_steps_not_from_a_second_source():
    crit = criteria("tip_calculator")
    n_steps = sum(len(c["steps"]) for c in crit["criteria"])
    numbered = lambda t: len(re.findall(r"^\s+\d+\. ", t, re.M))
    assert numbered(render_readable(crit)) == numbered(render_technical(crit)) == n_steps
    crit["criteria"][0]["steps"][-1]["equals"] = "99.99"      # change the structure: both renderings change with it
    assert "99.99" in render_readable(crit) and "99.99" in render_technical(crit)


def test_labels_without_a_label_are_humanised_and_bad_labels_are_rejected():
    crit = criteria("tip_calculator")
    crit.pop("labels")
    assert "Tip amount" in render_readable(crit)
    crit["labels"] = {"tip-amount": "tip-amount"}
    assert any("label" in p for p in validate_criteria(crit, ["index.html"], L))
    crit["labels"] = {"tip-amount": "[tip-amount]"}
    assert any("label" in p for p in validate_criteria(crit, ["index.html"], L))
    crit["labels"] = {"tip-amount": "Tip amount"}
    assert validate_criteria(crit, ["index.html"], L) == []


# ---------------------------------------------------------------- the variation rule (proposal time)
def _proposal(name, **kw):
    return CriteriaProposal(**{**single_example(name), **kw})


@pytest.mark.parametrize("name", list(APPS))
def test_reference_criteria_satisfy_the_variation_rule(name):
    assert check_criteria(CriteriaProposal(**criteria(name)), scope(name), L) == []


@pytest.mark.parametrize("name", list(APPS))
def test_single_example_criteria_are_sent_back_for_a_second_example(name):
    problems = check_criteria(_proposal(name), scope(name), L)
    assert problems and all("only one example" in p for p in problems)


def test_a_second_example_with_the_same_inputs_or_the_same_result_is_not_enough():
    c = single_example("tip_calculator")
    same_inputs = {**c["criteria"][0], "id": "C3"}
    c2 = {**c, "criteria": c["criteria"] + [same_inputs]}
    assert "R1" in " ".join(variation_problems(c2["criteria"], {"R1", "R2"}))
    full = criteria("tip_calculator")
    same_result = [dict(x) for x in full["criteria"]]
    for st in same_result[2]["steps"]:
        if st.get("action") == "assert_text":
            st["equals"] = {"tip-amount": "12.00", "total-amount": "92.00", "per-person": "23.00"}[st["target"]]
    assert "R1" in " ".join(variation_problems(same_result, {"R1", "R2"}))


def test_requirements_that_do_not_depend_on_input_need_no_second_example():
    c = [{"id": "C1", "title": "Heading", "requirement_ids": ["R1"],
          "steps": [{"action": "navigate", "page": "index.html"}, {"action": "assert_visible", "target": "heading"}]}]
    assert variation_problems(c, {"R1"}) == []


# ---------------------------------------------------------------- hardcoded outputs against the harness (real browser)
CASES = [("tip_hardcoded", "tip_calculator", {"C3", "C4"}), ("expense_static", "expense_tracker", {"C7", "C9"}),
         ("habit_static", "habit_tracker", {"C3"})]


@pytest.mark.parametrize("fixture,name,must_fail", CASES)
def test_hardcoded_apps_pass_one_example_but_fail_the_varied_criteria(tmp_path, fixture, name, must_fail):
    copy_adversarial(fixture, tmp_path / "app")
    gap = run(tmp_path, single_example(name))
    assert gap["all_passed"], "the fixture must demonstrate the gap: a single example is passed by a hardcoded app"
    varied = run(tmp_path, criteria(name))
    assert not varied["all_passed"] and must_fail <= set(failed_ids(varied))


@pytest.mark.parametrize("name", list(APPS))
def test_reference_apps_pass_the_varied_criteria(tmp_path, name):
    copy_app(name, tmp_path / "app")
    assert run(tmp_path, criteria(name))["all_passed"]


def test_correct_testids_and_labels_cannot_satisfy_wrong_behaviour(tmp_path):
    copy_app("tip_calculator", tmp_path / "app", {"index.html": ("const tip = bill * pct / 100,", "const tip = bill * pct / 100 + 1,")})
    rep = run(tmp_path, criteria("tip_calculator"))
    assert not rep["all_passed"] and {"C1", "C3"} <= set(failed_ids(rep))


# ---------------------------------------------------------------- the residual gap, stated as a test
def test_listed_example_lookup_passes_the_listed_criteria_and_unseen_inputs_expose_it(tmp_path):
    """Varied examples do not eliminate gaming: an app that special-cases exactly the listed examples passes them.
    A held-out input (what a protected course assessment would use; NOT part of the live acceptance checks) exposes it."""
    copy_adversarial("tip_lookup_listed", tmp_path / "app")
    assert run(tmp_path, criteria("tip_calculator"))["all_passed"]
    held_out = {"criteria": [{"id": "C1", "title": "held-out input", "requirement_ids": ["R1"], "steps": [
        {"action": "navigate", "page": "index.html"}, {"action": "enter", "target": "bill", "value": "45"},
        {"action": "select", "target": "tip-percent", "value": "20"}, {"action": "enter", "target": "people", "value": "2"},
        {"action": "click", "target": "calculate"}, {"action": "assert_text", "target": "tip-amount", "equals": "9.00"}]}]}
    assert not run(tmp_path, held_out)["all_passed"]
