"""SUPPLIED. The single last-working restore point, and the deterministic promotion safeguard.

Learners never write this logic. The agent calls ``promote`` after a trusted acceptance check and
``restore`` when a revision fails; both are deterministic and independent of model output.

Promotion rule (all must hold, otherwise nothing is saved and the reason is returned):
  * the report is a trusted *acceptance* report (not a diagnostic),
  * every criterion passed and there are no harness problems,
  * the report's files hash equals the files in ``app/`` right now,
  * the report's criteria hash equals the user-approved criteria right now,
  * no requirement is unresolved (unsupported/unverifiable and not yet replaced or removed by the user).
Only one restore point is kept: promoting replaces the previous one.
"""
from __future__ import annotations

import shutil

from ..config import Limits
from .store import ProjectStore, contract_hash, now, read_json, write_json
from .workspace import Workspace


class RestoreError(Exception):
    pass


def check_promotable(store: ProjectStore, pid: str, report: dict | None, limits: Limits) -> str | None:
    """Return None if the report may be promoted, else the reason it may not."""
    if not report:
        return "no trusted check has been run"
    if report.get("kind") != "acceptance":
        return "only a trusted acceptance report can be promoted (diagnostics never count)"
    if not report.get("all_passed") or report.get("problems"):
        return "not every approved criterion passed"
    approved = store.approved(pid)
    if not approved:
        return "no approved criteria exist for this project"
    if store.unresolved(pid):
        return "unresolved requirements remain, so this cannot be saved as a completed build: " + ", ".join(
            u["requirement_id"] for u in store.unresolved(pid))
    if report.get("criteria_hash") != contract_hash(approved["criteria"]):
        return "the report was produced for different criteria than the approved ones"
    if report.get("files_hash") != Workspace(store.app_dir(pid), limits).files_hash():
        return "the files changed after this report was produced"
    return None


def promote(store: ProjectStore, pid: str, report: dict | None, limits: Limits) -> dict:
    reason = check_promotable(store, pid, report, limits)
    if reason:
        return {"promoted": False, "reason": reason}
    d = store.restore_dir(pid)
    if d.exists():
        shutil.rmtree(d)
    (d / "app").mkdir(parents=True)
    ws = Workspace(store.app_dir(pid), limits)
    for f in ws.list_files():
        shutil.copyfile(store.app_dir(pid) / f["path"], d / "app" / f["path"])
    approved = store.approved(pid)
    write_json(d / "contract.json", {"scope": approved["scope"], "criteria": approved["criteria"]})
    manifest = {"saved_at": now(), "files_hash": report["files_hash"], "criteria_hash": report["criteria_hash"],
                "summary": report["summary"], "conditions": report.get("conditions", {}),
                "contract_version": approved["approval"]["version"]}
    write_json(d / "manifest.json", manifest)
    write_json(d / "report.json", report)
    store.update_meta(pid, restore_point=manifest)
    return {"promoted": True, "manifest": manifest}


def has_restore_point(store: ProjectStore, pid: str) -> bool:
    return (store.restore_dir(pid) / "manifest.json").is_file()


def restore(store: ProjectStore, pid: str, limits: Limits) -> dict:
    """Put the last verified files and approved contract back. Returns the stored acceptance report so the
    caller can derive status from it (the report's hashes match the restored files and contract)."""
    d = store.restore_dir(pid)
    manifest = read_json(d / "manifest.json")
    if not manifest:
        raise RestoreError("there is no restore point for this project yet")
    app = store.app_dir(pid)
    for f in Workspace(app, limits).list_files():
        (app / f["path"]).unlink()
    for f in sorted((d / "app").iterdir()):
        shutil.copyfile(f, app / f.name)
    contract = read_json(d / "contract.json")
    current = store.approved(pid)
    if not current or contract_hash(current["criteria"]) != manifest["criteria_hash"] \
            or contract_hash(current["scope"]) != contract_hash(contract["scope"]):
        store.approve_contract(pid, contract["scope"], contract["criteria"], source="restore", restoring=True)
    ws_hash = Workspace(app, limits).files_hash()
    if ws_hash != manifest["files_hash"]:  # pragma: no cover - would mean a corrupted restore point
        raise RestoreError("restored files do not match the restore point's recorded hash")
    return {"restored": True, "manifest": manifest, "report": read_json(d / "report.json")}
