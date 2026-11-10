"""Restore/promotion safeguard, regression planning, export/import hardening. No model involved."""
import io
import json
import os
import zipfile

import pytest

from appbuilder.config import Limits
from appbuilder.supplied import regression
from appbuilder.supplied.harness import run_checks
from appbuilder.supplied.portability import ImportRejected, export_project, import_project
from appbuilder.supplied.restore import RestoreError, promote, restore
from appbuilder.supplied.store import ProjectStore
from appbuilder.supplied.workspace import Workspace
from tests.fixtures_data import EXPENSE_BUG, copy_app, criteria, scope

L = Limits()
OWNER = "owner-aaaaaaaaaaaaaaaa"


@pytest.fixture
def proj(tmp_path):
    store = ProjectStore(tmp_path / "data")
    pid = store.create(OWNER, "Expenses")
    copy_app("expense_tracker", store.app_dir(pid))
    store.approve_contract(pid, scope("expense_tracker"), criteria("expense_tracker"))
    return store, pid


def _check(store, pid, kind="acceptance"):
    return run_checks(store.app_dir(pid), store.approved(pid)["criteria"], L, kind=kind)


def test_promotion_requires_a_trusted_passing_report_for_current_files(proj):
    store, pid = proj
    good = _check(store, pid)
    assert promote(store, pid, None, L)["promoted"] is False
    assert promote(store, pid, _check(store, pid, "diagnostic"), L)["reason"].startswith("only a trusted acceptance")
    forged = dict(good, all_passed=True, files_hash="0" * 64)
    assert "files changed" in promote(store, pid, forged, L)["reason"]
    forged2 = dict(good, criteria_hash="1" * 64)
    assert "different criteria" in promote(store, pid, forged2, L)["reason"]
    failing = dict(good, all_passed=False)
    assert "not every" in promote(store, pid, failing, L)["reason"]
    assert promote(store, pid, good, L)["promoted"] is True


def test_report_goes_stale_when_files_change(proj):
    store, pid = proj
    good = _check(store, pid)
    Workspace(store.app_dir(pid), L).write_file("index.html", "<p>changed</p>")
    assert promote(store, pid, good, L)["promoted"] is False


def test_failed_revision_is_restored_to_the_last_working_version(proj):
    store, pid = proj
    good = _check(store, pid)
    assert promote(store, pid, good, L)["promoted"]
    before = Workspace(store.app_dir(pid), L).files_hash()
    # a bad revision: broken dashboard AND a changed contract
    copy_app("expense_tracker", store.app_dir(pid), EXPENSE_BUG)
    c2 = criteria("expense_tracker")
    c2["criteria"] = c2["criteria"][:2]
    store.approve_contract(pid, scope("expense_tracker"), c2)
    bad = _check(store, pid)
    assert not bad["all_passed"]
    res = restore(store, pid, L)
    assert res["restored"] and Workspace(store.app_dir(pid), L).files_hash() == before
    assert len(store.approved(pid)["criteria"]["criteria"]) == 9          # the approved contract came back too
    assert store.approved(pid)["approval"]["source"] == "restore"
    assert _check(store, pid)["all_passed"]


def test_only_one_restore_point_is_kept(proj):
    store, pid = proj
    promote(store, pid, _check(store, pid), L)
    first = store.meta(pid)["restore_point"]["files_hash"]
    Workspace(store.app_dir(pid), L).write_file("extra.css", "body{}")
    promote(store, pid, _check(store, pid), L)
    assert store.meta(pid)["restore_point"]["files_hash"] != first
    assert sorted(p.name for p in store.restore_dir(pid).iterdir()) == ["app", "contract.json", "manifest.json", "report.json"]


def test_restore_without_a_restore_point_is_a_clear_error(proj):
    store, pid = proj
    with pytest.raises(RestoreError):
        restore(store, pid, L)


def test_regression_plan_labels_relevant_and_regression_checks(proj):
    store, pid = proj
    plan = regression.plan(criteria("expense_tracker"), ["R2"])
    assert plan == {"C1": "regression", "C2": "relevant", "C3": "regression", "C4": "regression", "C5": "regression",
                    "C6": "regression", "C7": "relevant", "C8": "regression", "C9": "regression"}
    copy_app("expense_tracker", store.app_dir(pid), EXPENSE_BUG)
    rep = run_checks(store.app_dir(pid), criteria("expense_tracker"), L, roles=plan)
    s = regression.summarise(rep)
    assert s["relevant_failed"] == ["C2", "C7"] and s["regressions"] == ["C4", "C9"] and not s["all_passed"]


# ---- export / import ---------------------------------------------------------------------------------
def test_export_contains_no_owner_token_or_secrets(proj, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "CANARY-SECRET-123")
    store, pid = proj
    data = export_project(store, pid, L)
    z = zipfile.ZipFile(io.BytesIO(data))
    blob = b"".join(z.read(n) for n in z.namelist())
    assert OWNER.encode() not in blob and b"CANARY" not in blob
    assert sorted(z.namelist()) == ["app/dashboard.html", "app/index.html", "app/settings.html", "manifest.json"]


def test_import_is_untrusted_has_no_approval_and_must_be_rechecked(proj, tmp_path):
    store, pid = proj
    data = export_project(store, pid, L)
    info = import_project(store, "owner-bbbbbbbbbbbbbbbb", data, L)
    new = info["id"]
    assert info["status"] == "imported_untrusted" and info["has_proposal"]
    assert store.approved(new) is None                       # nothing is approved until the user re-approves
    assert store.meta(new)["owner"] == "owner-bbbbbbbbbbbbbbbb" and new != pid
    # checks only run once criteria are approved again, and are then rerun from scratch
    prop = json.loads((store.imported_dir(new) / "proposal.json").read_text())["proposal"]
    store.approve_contract(new, prop["scope"], prop["criteria"], source="user_reapproval")
    assert _check(store, new)["all_passed"]


def _zip(entries, manifest=True, **kw):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if manifest:
            z.writestr("manifest.json", json.dumps({"format": "appbuilder-export/1", "title": "x"}))
        for n, c in entries.items():
            z.writestr(n, c)
    return buf.getvalue()


@pytest.mark.parametrize("entries,needle", [
    ({"app/../evil.html": "x"}, "unexpected entry"),
    ({"../evil.html": "x"}, "unexpected entry"),
    ({"/abs.html": "x"}, "unexpected entry"),
    ({"app/sub/a.html": "x"}, "unexpected entry"),
    ({"app/run.py": "import os"}, "not an allowed"),
    ({"app/.hidden.html": "x"}, "not an allowed"),
    ({"app/a.html": "x", "notes.txt": "x"}, "unexpected entry"),
    ({}, "no app files"),
])
def test_import_rejects_hostile_archives(proj, entries, needle):
    store, _ = proj
    with pytest.raises(ImportRejected) as e:
        import_project(store, OWNER, _zip(entries), L)
    assert needle in e.value.reason
    assert len(store.list(OWNER)) == 1                       # nothing was created by the rejected import


def test_import_rejects_oversized_bombs_symlinks_and_bad_manifests(proj):
    store, _ = proj
    big = os.urandom(L.max_file_bytes // 2 + 1).hex()      # incompressible, just over the per-file limit
    with pytest.raises(ImportRejected, match="too large"):
        import_project(store, OWNER, _zip({"app/a.html": big}), L)
    with pytest.raises(ImportRejected, match="compression ratio"):
        import_project(store, OWNER, _zip({"app/a.html": "0" * 60_000}), L)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": "appbuilder-export/1"}))
        zi = zipfile.ZipInfo("app/link.html")
        zi.external_attr = (0o120777 << 16)
        z.writestr(zi, "/etc/passwd")
    with pytest.raises(ImportRejected, match="link"):
        import_project(store, OWNER, buf.getvalue(), L)
    with pytest.raises(ImportRejected, match="manifest"):
        import_project(store, OWNER, _zip({"app/a.html": "x"}, manifest=False), L)
    with pytest.raises(ImportRejected, match="not a valid zip"):
        import_project(store, OWNER, b"not a zip", L)
    with pytest.raises(ImportRejected, match="too large"):
        import_project(store, OWNER, b"0" * 1_100_000, L)


def test_imported_criteria_that_fail_validation_are_dropped_not_trusted(proj):
    store, _ = proj
    bad = {"format": "appbuilder-export/1", "title": "t", "scope": {}, "criteria": {"criteria": [
        {"id": "C1", "title": "t", "requirement_ids": ["R1"], "steps": [{"action": "run_shell", "cmd": "rm -rf /"}]}]}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(bad))
        z.writestr("app/index.html", "<p>hi</p>")
    info = import_project(store, OWNER, buf.getvalue(), L)
    assert not info["has_proposal"] and any("not usable" in n for n in info["notes"])
    assert store.approved(info["id"]) is None
