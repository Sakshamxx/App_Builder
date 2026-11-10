"""SUPPLIED. Export and hardened import.

Export writes a zip with the app files and the *claimed* scope and criteria. It never includes the
owner token, credentials, run logs or model output. Import treats the archive as untrusted input:
  * bounded archive size, entry count, per-file and total uncompressed size, compression ratio;
  * entry names must be flat, allow-listed files (no folders, traversal, absolute paths, symlinks);
  * the manifest's hashes are advisory only. Nothing in the archive is trusted: the project is
    created as ``imported_untrusted``, has NO approved criteria, and the imported criteria are kept
    only as a *proposal* that the user must re-approve before any trusted check runs.
"""
from __future__ import annotations

import io
import json
import re
import stat
import zipfile

from ..config import Limits
from .criteria_vocab import validate_criteria
from .store import ProjectStore, StoreError, now, read_json, write_json
from .workspace import ALLOWED_EXT, NAME_RE, Workspace, WorkspaceError

FORMAT = "appbuilder-export/1"
MAX_ARCHIVE_BYTES = 1_000_000
MAX_RATIO = 50


class ImportRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


RID = re.compile(r"^R[0-9]{1,2}$")


def clean_unresolved(raw: object) -> list[dict]:
    """Imported unresolved entries are untrusted text: keep only well-formed ones, bounded and string-typed."""
    out = []
    for u in raw if isinstance(raw, list) else []:
        if isinstance(u, dict) and isinstance(u.get("requirement_id"), str) and RID.match(u["requirement_id"]) and len(out) < 20:
            out.append({"requirement_id": u["requirement_id"], "reason": str(u.get("reason") or "")[:500],
                        "text": str(u.get("text") or "")[:500],
                        "proposed_alternative": (str(u["proposed_alternative"])[:500] if u.get("proposed_alternative") else None),
                        "origin": u.get("origin") if u.get("origin") in ("initial", "revision") else "initial"})
    return out


def export_project(store: ProjectStore, pid: str, limits: Limits) -> bytes:
    meta = store.meta(pid)
    ws = Workspace(store.app_dir(pid), limits)
    approved = store.approved(pid)
    pending = read_json(store.runs_dir(pid) / "pending.json") or {}
    proposal = pending if pending.get("kind") in ("criteria", "revision") and pending.get("criteria") else None
    # unresolved requirements travel with the project; an unapproved proposal is exported as a claimed proposal, not as approved
    src = approved or proposal
    manifest = {
        "format": FORMAT, "exported_at": now(), "title": meta["title"],
        "files": [f["path"] for f in ws.list_files()], "files_hash": ws.files_hash(),
        "scope": src["scope"] if src else None,
        "criteria": src["criteria"] if src else None,
        "claimed_approved": bool(approved),
        "unresolved": store.unresolved(pid),
        "resolutions": (approved or {}).get("resolutions", []),
        "note": "Imported projects are untrusted: criteria must be re-approved and checks re-run. "
                "Unresolved requirements stay unresolved until the importing user replaces or removes them.",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        for f in ws.list_files():
            z.writestr(f"app/{f['path']}", (store.app_dir(pid) / f["path"]).read_bytes())
    return buf.getvalue()


def _read_archive(data: bytes, limits: Limits) -> tuple[dict, dict[str, str]]:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ImportRejected("archive is too large")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportRejected("not a valid zip archive")
    infos = z.infolist()
    if len(infos) > limits.max_files + 1:
        raise ImportRejected("archive has too many entries")
    if sum(i.file_size for i in infos) > limits.max_total_bytes + 200_000:
        raise ImportRejected("archive expands to too much data")
    files: dict[str, str] = {}
    manifest = None
    for i in infos:
        mode = i.external_attr >> 16
        if i.is_dir() or stat.S_ISLNK(mode):
            raise ImportRejected(f"entry {i.filename!r} is a folder or link")
        if i.flag_bits & 0x1:
            raise ImportRejected("encrypted entries are not accepted")
        if i.compress_size and i.file_size / i.compress_size > MAX_RATIO and i.file_size > 10_000:
            raise ImportRejected(f"entry {i.filename!r} has a suspicious compression ratio")
        if i.file_size > limits.max_file_bytes:
            raise ImportRejected(f"entry {i.filename!r} is too large")
        name = i.filename
        if name == "manifest.json":
            try:
                manifest = json.loads(z.read(i).decode("utf-8"))
            except Exception:
                raise ImportRejected("manifest.json is not valid JSON")
            continue
        if not name.startswith("app/") or name.count("/") != 1:
            raise ImportRejected(f"unexpected entry {name!r}")
        base = name[4:]
        if not NAME_RE.match(base) or "." + base.rsplit(".", 1)[-1].lower() not in ALLOWED_EXT:
            raise ImportRejected(f"entry {name!r} is not an allowed file name or type")
        try:
            files[base] = z.read(i).decode("utf-8")
        except UnicodeDecodeError:
            raise ImportRejected(f"entry {name!r} is not UTF-8 text")
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ImportRejected("missing or unsupported manifest")
    if not files:
        raise ImportRejected("archive contains no app files")
    return manifest, files


def import_project(store: ProjectStore, owner: str, data: bytes, limits: Limits) -> dict:
    manifest, files = _read_archive(data, limits)
    title = str(manifest.get("title") or "Imported app")[:100]
    pid = store.create(owner, title + " (imported)", status="imported_untrusted")
    try:
        ws = Workspace(store.app_dir(pid), limits)
        for name, content in files.items():
            ws.write_file(name, content)
    except WorkspaceError as e:
        store.delete(pid)
        raise ImportRejected(f"files rejected: {e.message}")
    notes = []
    proposal = None
    crit, scope = manifest.get("criteria"), manifest.get("scope")
    if isinstance(crit, dict) and isinstance(scope, dict):
        problems = validate_criteria(crit, ws.page_names(), limits)
        if problems:
            notes.append("imported criteria were not usable: " + "; ".join(problems)[:300])
        else:
            proposal = {"scope": scope, "criteria": crit}
    else:
        notes.append("the archive carried no criteria")
    d = store.imported_dir(pid)
    d.mkdir(exist_ok=True)
    write_json(d / "proposal.json", {"proposal": proposal, "notes": notes, "claimed_files_hash": manifest.get("files_hash")})
    unresolved = clean_unresolved(manifest.get("unresolved"))
    if unresolved:
        notes.append("unresolved requirements came with this project: " + ", ".join(u["requirement_id"] for u in unresolved))
    store.update_meta(pid, stop_reason=None, unresolved=unresolved, imported={"notes": notes, "has_proposal": proposal is not None})
    return {"id": pid, "status": "imported_untrusted", "files": sorted(files), "has_proposal": proposal is not None,
            "unresolved": unresolved, "notes": notes, "message": "Imported as untrusted: review and approve the criteria, then run the checks."}


__all__ = ["export_project", "import_project", "ImportRejected", "StoreError"]
