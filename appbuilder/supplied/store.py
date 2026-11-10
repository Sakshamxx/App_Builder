"""SUPPLIED. Project storage: one folder per project, scoped to an owner token.

    projects/<id>/
        meta.json            status, stop reason, last build summary
        app/                 the generated app (the only folder the agent's file tools can touch)
        approved/            approved scope + criteria + approval record (agent file tools cannot reach it)
        imported/            untrusted imported scope/criteria/history, kept apart from approved/
        restore/             last verified working version (see restore.py)
        runs/                trace files and check reports

"Same-browser" saving: the browser keeps a random owner token in localStorage; the store only lists
projects for that token. It is an identifier, not a credential, and it is not a security boundary
between people who share a machine. Export/import moves a project between browsers.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

PID_RE = re.compile(r"^p[0-9a-f]{20}$")
OWNER_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class StoreError(Exception):
    pass


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def contract_hash(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


class ProjectStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve() / "projects"
        self.root.mkdir(parents=True, exist_ok=True)

    # --- paths (project ids are validated before any path is built) ---
    def dir(self, pid: str) -> Path:
        if not isinstance(pid, str) or not PID_RE.match(pid):
            raise StoreError("invalid project id")
        return self.root / pid

    def app_dir(self, pid: str) -> Path:
        return self.dir(pid) / "app"

    def approved_dir(self, pid: str) -> Path:
        return self.dir(pid) / "approved"

    def imported_dir(self, pid: str) -> Path:
        return self.dir(pid) / "imported"

    def restore_dir(self, pid: str) -> Path:
        return self.dir(pid) / "restore"

    def runs_dir(self, pid: str) -> Path:
        return self.dir(pid) / "runs"

    def exists(self, pid: str) -> bool:
        try:
            return self.dir(pid).is_dir()
        except StoreError:
            return False

    # --- lifecycle ---
    def create(self, owner: str, title: str, status: str = "draft") -> str:
        if not OWNER_RE.match(owner or ""):
            raise StoreError("invalid owner token")
        pid = "p" + secrets.token_hex(10)
        for sub in ("app", "approved", "runs"):
            (self.dir(pid) / sub).mkdir(parents=True)
        write_json(self.dir(pid) / "meta.json", {
            "id": pid, "owner": owner, "title": title[:120], "status": status,
            "stop_reason": None, "created_at": now(), "updated_at": now(), "last_build": None,
        })
        return pid

    def meta(self, pid: str) -> dict:
        m = read_json(self.dir(pid) / "meta.json")
        if m is None:
            raise StoreError("no such project")
        return m

    def update_meta(self, pid: str, **fields) -> dict:
        m = self.meta(pid)
        m.update(fields)
        m["updated_at"] = now()
        write_json(self.dir(pid) / "meta.json", m)
        return m

    def get_for_owner(self, owner: str, pid: str) -> dict:
        m = self.meta(pid)
        if m.get("owner") != owner:
            raise StoreError("no such project")  # same message as missing: do not reveal existence
        return m

    def list(self, owner: str) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()):
            m = read_json(d / "meta.json") if d.is_dir() else None
            if m and m.get("owner") == owner:
                out.append({k: m.get(k) for k in ("id", "title", "status", "stop_reason", "updated_at")})
        return sorted(out, key=lambda x: x["updated_at"] or "", reverse=True)

    def delete(self, pid: str) -> None:
        shutil.rmtree(self.dir(pid), ignore_errors=True)

    # --- approved contract (written only through approve_contract) ---
    def approved(self, pid: str) -> dict | None:
        d = self.approved_dir(pid)
        approval = read_json(d / "approval.json")
        if not approval:
            return None
        return {"scope": read_json(d / "scope.json"), "criteria": read_json(d / "criteria.json"), "approval": approval,
                "resolutions": read_json(d / "resolutions.json", [])}

    def unresolved(self, pid: str) -> list[dict]:
        """Requirements that cannot be checked and have not been replaced or removed by the user (kept in meta so they
        survive proposals being replaced, restore, export and import)."""
        return list(self.meta(pid).get("unresolved") or [])

    def approve_contract(self, pid: str, scope: dict, criteria: dict, source: str = "user_approval", *,
                         resolutions: list[dict] | None = None, restoring: bool = False) -> dict:
        """Record the user's approval of scope + criteria. Returns the approval record.
        The caller (the agent layer) is responsible for only calling this after the user said yes.
        Refused while any requirement is unresolved: an approved contract is always complete. ``restoring`` is only for putting
        back a contract that was approved complete earlier (see restore.py)."""
        if not restoring and self.unresolved(pid):
            raise StoreError("unresolved requirements remain: " + ", ".join(u["requirement_id"] for u in self.unresolved(pid)))
        d = self.approved_dir(pid)
        prev = read_json(d / "approval.json")
        version = (prev["version"] + 1) if prev else 1
        if prev:
            hist = d / "history"
            hist.mkdir(exist_ok=True)
            write_json(hist / f"v{prev['version']}.json", {
                "scope": read_json(d / "scope.json"), "criteria": read_json(d / "criteria.json"), "approval": prev})
        write_json(d / "scope.json", scope)
        write_json(d / "criteria.json", criteria)
        if resolutions:   # the user's explicit replace/remove decisions are an append-only record
            write_json(d / "resolutions.json", (read_json(d / "resolutions.json", []) or []) + resolutions)
        rec = {"version": version, "scope_hash": contract_hash(scope), "criteria_hash": contract_hash(criteria),
               "approved_at": now(), "source": source, "unresolved": []}
        write_json(d / "approval.json", rec)
        return rec
