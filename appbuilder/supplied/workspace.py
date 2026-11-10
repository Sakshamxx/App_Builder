"""SUPPLIED. The confined workspace that the agent's file tools operate on.

Boundary (from the first real tool use onward): flat file names only, an extension allow-list, size
and count limits, no symlinks, and nothing outside ``app/``. The approved requirements and criteria
live in a sibling folder this class cannot reach, so the generated app (and the agent's file tools)
can never rewrite the contract they are checked against.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..config import Limits

ALLOWED_EXT = {".html", ".css", ".js"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Workspace:
    def __init__(self, root: Path, limits: Limits):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.limits = limits

    def _resolve(self, name: object) -> Path:
        if not isinstance(name, str) or not name:
            raise WorkspaceError("bad_path", "path must be a non-empty string")
        if "/" in name or "\\" in name or "\x00" in name or name.startswith("."):
            raise WorkspaceError("bad_path", "only flat file names are allowed (no folders, no dot-files)")
        if not NAME_RE.match(name):
            raise WorkspaceError("bad_path", "file name may use letters, digits, '.', '_' and '-' only")
        if Path(name).suffix.lower() not in ALLOWED_EXT:
            raise WorkspaceError("bad_extension", f"extension not allowed; allowed: {sorted(ALLOWED_EXT)}")
        p = (self.root / name)
        if p.is_symlink():
            raise WorkspaceError("symlink", "symbolic links are not allowed")
        if p.resolve().parent != self.root.resolve():
            raise WorkspaceError("outside_workspace", "path resolves outside the workspace")
        return p

    def list_files(self) -> list[dict]:
        return [
            {"path": p.name, "bytes": p.stat().st_size}
            for p in sorted(self.root.iterdir())
            if p.is_file() and not p.is_symlink()
        ]

    def read_file(self, name: str) -> str:
        p = self._resolve(name)
        if not p.is_file():
            raise WorkspaceError("not_found", f"no such file: {name}")
        return p.read_text(encoding="utf-8")

    def write_file(self, name: str, content: object) -> dict:
        p = self._resolve(name)
        if not isinstance(content, str):
            raise WorkspaceError("bad_content", "content must be a string")
        data = content.encode("utf-8")
        if len(data) > self.limits.max_file_bytes:
            raise WorkspaceError("too_large", f"file exceeds {self.limits.max_file_bytes} bytes")
        existing = {f["path"]: f["bytes"] for f in self.list_files()}
        if name not in existing and len(existing) >= self.limits.max_files:
            raise WorkspaceError("too_many_files", f"at most {self.limits.max_files} files")
        total = sum(v for k, v in existing.items() if k != name) + len(data)
        if total > self.limits.max_total_bytes:
            raise WorkspaceError("too_large", f"workspace exceeds {self.limits.max_total_bytes} bytes in total")
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return {"path": name, "bytes": len(data)}

    def delete_file(self, name: str) -> None:
        p = self._resolve(name)
        if p.is_file():
            p.unlink()

    def page_names(self) -> list[str]:
        return [f["path"] for f in self.list_files() if f["path"].endswith(".html")]

    def files_hash(self) -> str:
        h = hashlib.sha256()
        for p in sorted(self.root.iterdir()):
            if p.is_file() and not p.is_symlink():
                h.update(p.name.encode() + b"\x00" + p.read_bytes() + b"\x00")
        return h.hexdigest()
