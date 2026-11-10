"""Unsupported/unverifiable requirements are never silently dropped from acceptance.

Rule under test: while any requirement is unresolved, nothing can be approved, built, promoted or reported as verified. The
only ways forward are the user's explicit decisions: a supported replacement, or removal from scope. There is no
"keep unresolved and approve". Scripted stub model: this tests our code's behaviour, not a real model's judgement.
"""
import json
import zipfile
import io

import pytest

from appbuilder.agent.service import UserError
from appbuilder.config import Limits
from appbuilder.supplied.restore import promote
from appbuilder.supplied.store import ProjectStore, StoreError
from appbuilder.testing.scripted import calls, js, write
from tests.fixtures_data import criteria, read_app, scope
from tests.helpers import OWNER, OWNER2, RUN, drive_to_build, make_service, new_verified, scope_reply, write_all

IMPLS = ["handwritten", "framework"]
pytestmark = pytest.mark.parametrize("impl", IMPLS)

ALT = "The page shows the last tip percentage used in the current session, without saving it."


def tip_with_unverifiable(alt=ALT):
    c = criteria("tip_calculator")
    c["criteria"] = [c["criteria"][0], c["criteria"][2]]                       # R1 only (two examples); R2 cannot be checked
    return js({**c, "unverifiable": [{"requirement_id": "R2", "reason": "persistence across browser restarts cannot be simulated",
                                      "proposed_alternative": alt}]})


def at_unresolved(tmp_path, impl):
    svc = make_service(tmp_path, impl, [scope_reply("tip_calculator"), tip_with_unverifiable()])
    st = svc.create(OWNER, "tip calculator")
    st = svc.approve_scope(OWNER, st["id"], st["pending"]["scope_hash"])
    return svc, st["id"], st


def replacement_reply():
    c = criteria("tip_calculator")
    both = [dict(x, requirement_ids=["R2"]) for x in (c["criteria"][1], c["criteria"][3])]
    return js({"criteria": both, "labels": c["labels"], "unverifiable": []})


def test_unresolved_requirement_is_shown_blocks_approval_and_is_never_called_verified(tmp_path, impl):
    svc, pid, st = at_unresolved(tmp_path, impl)
    assert st["status"]["status"] == "unresolved_requirements" and st["status"]["unresolved"] == ["R2"]
    r = st["pending"]["readable"]
    assert "UNRESOLVED" in r and "R2" in r and "Remembers the last tip percentage" in r and ALT in r
    assert [u["requirement_id"] for u in st["unresolved"]] == ["R2"]
    with pytest.raises(UserError, match="R2"):
        svc.approve_criteria(OWNER, pid, st["pending"]["criteria_hash"])
    with pytest.raises(UserError):
        svc.build(OWNER, pid)
    assert svc.store.approved(pid) is None
    # the supplied store itself refuses, independent of the agent layer
    with pytest.raises(StoreError, match="unresolved"):
        svc.store.approve_contract(pid, st["pending"]["scope"], st["pending"]["criteria"])
    assert svc.test_transport.i == 2                          # no build call was made


def test_there_is_no_keep_unresolved_and_approve_path(tmp_path, impl):
    svc, pid, st = at_unresolved(tmp_path, impl)
    for action in ("keep", "approve", "ignore", ""):
        with pytest.raises(UserError):
            svc.resolve_item(OWNER, pid, "R2", action)
    with pytest.raises(UserError):
        svc.resolve_item(OWNER, pid, "R2", "replace", "")          # a replacement has to be written down
    assert svc.store.unresolved(pid)


def test_replacing_with_a_supported_requirement_gets_checks_then_can_be_approved_and_built(tmp_path, impl):
    svc, pid, st = at_unresolved(tmp_path, impl)
    svc.test_transport.script += [replacement_reply(), write_all("tip_calculator"), RUN]
    st = svc.resolve_item(OWNER, pid, "R2", "replace", ALT)
    assert st["unresolved"] == [] and st["status"]["status"] == "awaiting_criteria_approval"
    reqs = {r["id"]: r["text"] for r in st["pending"]["scope"]["requirements"]}
    assert reqs["R2"] == ALT and "UNRESOLVED" not in st["pending"]["readable"]
    assert [c["id"] for c in st["pending"]["criteria"]["criteria"]] == ["C1", "C3", "C4", "C5"]      # the two replacement checks are appended with fresh ids
    st = svc.approve_criteria(OWNER, pid, st["pending"]["criteria_hash"])
    a = svc.store.approved(pid)
    assert a["approval"]["unresolved"] == [] and a["resolutions"][0]["action"] == "replace" and a["resolutions"][0]["replacement_text"] == ALT
    assert a["resolutions"][0]["original_text"].startswith("Remembers the last tip percentage")      # the original stays on record
    assert st["status"]["status"] == "verified"


def test_removing_a_requirement_is_an_explicit_recorded_decision(tmp_path, impl):
    svc, pid, st = at_unresolved(tmp_path, impl)
    svc.test_transport.script += [write_all("tip_calculator"), RUN]
    st = svc.resolve_item(OWNER, pid, "R2", "remove")
    assert st["unresolved"] == [] and [r["id"] for r in st["pending"]["scope"]["requirements"]] == ["R1"]
    st = svc.approve_criteria(OWNER, pid, st["pending"]["criteria_hash"])
    a = svc.store.approved(pid)
    assert [r["id"] for r in a["scope"]["requirements"]] == ["R1"]
    assert a["resolutions"][0]["action"] == "remove" and a["resolutions"][0]["original_text"]
    assert "Remembers" not in st["approved"]["scope_text"] and st["status"]["status"] == "verified"


def test_a_replacement_that_is_still_unverifiable_stays_unresolved(tmp_path, impl):
    svc, pid, st = at_unresolved(tmp_path, impl)
    svc.test_transport.script += [js({"criteria": [], "unverifiable": [{"requirement_id": "R2", "reason": "still not checkable"}]})]
    st = svc.resolve_item(OWNER, pid, "R2", "replace", "The tip button glows when hovered.")
    assert [u["requirement_id"] for u in st["unresolved"]] == ["R2"] and st["status"]["status"] == "unresolved_requirements"
    with pytest.raises(UserError):
        svc.approve_criteria(OWNER, pid, st["pending"]["criteria_hash"])


# ---------------------------------------------------------------- revisions of an existing, verified project
JUDGE = {"verdict": "compatible", "explanation": "adds a feature", "touched_requirement_ids": [],
         "added_requirements": [{"id": "R6", "text": "The total glows green when it is above 100."}],
         "conflicting_requirement_ids": [], "clarification_question": None}


def verified_with_pending_revision(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    svc.test_transport.script += [js(JUDGE), js({"criteria": [], "unverifiable": [
        {"requirement_id": "R6", "reason": "visual appearance cannot be checked", "proposed_alternative": "Show the word HIGH next to totals above 100."}]})]
    st = svc.revise(OWNER, pid, "make the total glow green above 100")
    return svc, pid, st


def test_an_unverifiable_revision_cannot_be_approved_and_the_project_is_not_called_verified(tmp_path, impl):
    svc, pid, st = verified_with_pending_revision(tmp_path, impl)
    assert st["status"]["status"] == "unresolved_requirements" and st["status"]["partial_draft"]
    assert "R6" in st["pending"]["readable"] and "glows green" in st["pending"]["readable"]
    with pytest.raises(UserError):
        svc.approve_revision(OWNER, pid, st["pending"]["criteria_hash"])
    with pytest.raises(UserError):
        svc.revise(OWNER, pid, "something else")
    assert svc.store.approved(pid)["approval"]["version"] == 1
    report = json.loads((svc.store.runs_dir(pid) / "last_acceptance.json").read_text())
    assert promote(svc.store, pid, report, Limits())["reason"].startswith("unresolved requirements remain")


def test_removing_the_only_unverifiable_addition_cancels_the_revision(tmp_path, impl):
    svc, pid, st = verified_with_pending_revision(tmp_path, impl)
    st = svc.resolve_item(OWNER, pid, "R6", "remove")
    assert st["pending"] is None and st["unresolved"] == [] and st["status"]["status"] == "verified"


def test_restore_keeps_unresolved_requirements_and_their_proposal_visible(tmp_path, impl):
    svc, pid, st = verified_with_pending_revision(tmp_path, impl)
    st = svc.restore_last_working(OWNER, pid)
    assert [u["requirement_id"] for u in st["unresolved"]] == ["R6"] and st["pending"] and st["status"]["status"] == "unresolved_requirements"


def test_export_and_import_carry_unresolved_requirements_and_block_approval(tmp_path, impl):
    svc, pid, st = verified_with_pending_revision(tmp_path, impl)
    data = svc.export(OWNER, pid)
    man = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))
    assert man["unresolved"][0]["requirement_id"] == "R6" and "glows green" in man["unresolved"][0]["text"]
    st = svc.import_archive(OWNER2, data)
    nid = st["id"]
    assert [u["requirement_id"] for u in st["unresolved"]] == ["R6"] and st["status"]["status"] == "unresolved_requirements"
    assert "UNRESOLVED" in st["pending"]["readable"] and "NOT TRUSTED" in st["pending"]["readable"]
    with pytest.raises(UserError):
        svc.approve_criteria(OWNER2, nid, st["pending"]["criteria_hash"])
    assert svc.store.approved(nid) is None
    st = svc.resolve_item(OWNER2, nid, "R6", "remove")
    st = svc.approve_criteria(OWNER2, nid, st["pending"]["criteria_hash"])
    assert st["recheck"]["all_passed"] and st["status"]["status"] == "verified"


def test_a_forged_unresolved_list_in_an_import_is_bounded_and_cleaned(tmp_path, impl):
    svc, pid = new_verified(tmp_path, impl)
    z = io.BytesIO()
    src = zipfile.ZipFile(io.BytesIO(svc.export(OWNER, pid)))
    man = json.loads(src.read("manifest.json"))
    man["unresolved"] = [{"requirement_id": "../../etc", "reason": "x"}, {"requirement_id": "R9", "reason": "y" * 5000, "text": 5}] + ["junk"] * 50
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("manifest.json", json.dumps(man))
        for n in src.namelist():
            if n != "manifest.json":
                out.writestr(n, src.read(n))
    st = svc.import_archive(OWNER2, z.getvalue())
    assert [u["requirement_id"] for u in st["unresolved"]] == ["R9"] and len(st["unresolved"][0]["reason"]) == 500
