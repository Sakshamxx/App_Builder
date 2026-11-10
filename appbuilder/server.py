"""SUPPLIED. HTTP API + static UI around the learner-owned ``AppBuilderService``.

Credentials: the model key is read from the environment into ``Settings`` once, lives only in the transport object, and is never
returned by any endpoint. Browser sessions are identified by a random owner token kept in localStorage (identifier, not credential).
Generated apps are served by this same app at ``/preview/<project id>/<file>`` with the strict CSP from ``supplied/preview.py``
plus a CSP ``sandbox`` (opaque origin, so a generated page can never reach the builder's origin, its localStorage or its API token).

HOSTED DEMO: the server binds 0.0.0.0 on ``$PORT`` (default 8080). If ``APP_PASSWORD`` is set, everything except ``/healthz`` and
``/preview/<id>/...`` needs HTTP Basic auth (any username). This is a shared password, NOT per-user authorisation, and it is NOT
operating-system isolation of model-written code (see DEPLOYMENT_BLOCKERS.md). Do not run it with a real model key unless you accept that.
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import json
import re
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .agent.service import AppBuilderService, Busy, UserError
from .config import Settings
from .supplied.preview import csp, static_file
from .supplied.store import OWNER_RE, PID_RE, StoreError

PREVIEW_RE = re.compile(r"^/preview/p[0-9a-f]{20}/")      # the only path prefix that is exempt from the password gate
PREVIEW_SANDBOX = "sandbox allow-scripts allow-forms"       # forced on every preview response (also when opened top-level)


UI = Path(__file__).parent / "supplied" / "ui"
UI_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
          "frame-src 'self'; base-uri 'self'; form-action 'self'; object-src 'none'; frame-ancestors 'none'")


def create_app(settings: Settings, service: AppBuilderService | None = None) -> FastAPI:
    svc = service or AppBuilderService(settings)
    app = FastAPI(title="App Builder Agent (Project 1 reference)", docs_url=None, redoc_url=None, openapi_url=None)

    def owner(x_owner_token: str = Header(default="")) -> str:
        if not OWNER_RE.match(x_owner_token):
            raise HTTPException(401, "missing or invalid owner token")
        return x_owner_token

    @app.exception_handler(UserError)
    async def _user(_, e):
        return JSONResponse({"error": str(e)}, 400)

    @app.exception_handler(Busy)
    async def _busy(_, e):
        return JSONResponse({"error": str(e)}, 409)

    @app.exception_handler(StoreError)
    async def _nf(_, e):
        return JSONResponse({"error": "no such project"}, 404)

    def password_ok(authorization: str) -> bool:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            _user, sep, given = base64.b64decode(token.strip(), validate=True).decode("utf-8").partition(":")
        except ValueError:
            return False
        return bool(sep) and hmac.compare_digest(given.encode(), settings.app_password.encode())

    @app.middleware("http")            # added first, so it runs inside the headers middleware below
    async def password_gate(request: Request, call_next):
        path = request.url.path
        if settings.app_password and path != "/healthz" and not PREVIEW_RE.match(path) \
                and not password_ok(request.headers.get("authorization", "")):
            return Response("Password required", 401, headers={"WWW-Authenticate": 'Basic realm="App Builder", charset="UTF-8"'})
        return await call_next(request)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        r = await call_next(request)
        if not PREVIEW_RE.match(request.url.path):      # preview responses carry their own (stricter) policy
            r.headers["Content-Security-Policy"] = UI_CSP
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["Referrer-Policy"] = "no-referrer"
        r.headers["Cache-Control"] = "no-store"
        return r

    def pid_ok(pid: str) -> str:
        if not PID_RE.match(pid):
            raise HTTPException(404, "no such project")
        return pid

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    # ---- generated apps, served from this app (no password: see the module docstring; the files are flat, allow-listed, sandboxed)
    @app.get("/preview/{pid}/{name:path}")
    def preview(pid: str, name: str):
        if not PID_RE.match(pid) or not svc.store.exists(pid):
            raise HTTPException(404, "no such project")
        hit = static_file(svc.store.app_dir(pid), name or "index.html")
        if hit is None:
            raise HTTPException(404, "not found")
        path, ctype = hit
        return Response(path.read_bytes(), media_type=ctype, headers={
            "Content-Security-Policy": csp("'self'") + "; " + PREVIEW_SANDBOX, "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})

    # ---- config (never includes the key)
    @app.get("/api/config")
    async def config():
        return {"provider": settings.provider, "model": settings.model, "implementation": settings.implementation,
                "demo": settings.provider == "demo", "live_model_configured": settings.live_model_configured(),
                "structured_mode": settings.structured_mode,
                "limits": {"max_model_turns": settings.limits.max_model_turns, "max_repairs": settings.limits.max_repairs}}

    @app.get("/api/projects")
    async def projects(o: str = Depends(owner)):
        return svc.list(o)

    @app.post("/api/projects")
    async def create(req: Request, o: str = Depends(owner)):
        body = await req.json()
        return await run_in_threadpool(svc.create, o, str(body.get("request", "")))

    @app.get("/api/projects/{pid}")
    async def get(pid: str, o: str = Depends(owner)):
        return svc.state(o, pid_ok(pid))

    @app.delete("/api/projects/{pid}")
    async def delete(pid: str, o: str = Depends(owner)):
        svc.delete(o, pid_ok(pid))
        return {"ok": True}

    async def act(fn, *a):
        return await run_in_threadpool(fn, *a)

    @app.post("/api/projects/{pid}/{action}")
    async def action(pid: str, action: str, req: Request, o: str = Depends(owner)):
        pid_ok(pid)
        body = await req.json() if req.headers.get("content-length", "0") not in ("0", "") else {}
        s = lambda k: str(body.get(k, ""))
        table = {
            "approve-scope": lambda: svc.approve_scope(o, pid, s("hash")),
            "approve-criteria": lambda: svc.approve_criteria(o, pid, s("hash")),
            "approve-revision": lambda: svc.approve_revision(o, pid, s("hash")),
            "revise-proposal": lambda: svc.revise_proposal(o, pid, s("feedback")),
            "accept-alternative": lambda: svc.accept_alternative(o, pid),
            "revise": lambda: svc.revise(o, pid, s("request")),
            "resolve": lambda: svc.resolve_clarification(o, pid, s("choice")),
            "resolve-item": lambda: svc.resolve_item(o, pid, s("requirement_id"), s("action"), s("replacement") or None),
            "restore": lambda: svc.restore_last_working(o, pid),
            "rebuild": lambda: svc.build(o, pid),
        }
        if action not in table:
            raise HTTPException(404, "unknown action")
        return await act(table[action])

    @app.get("/api/projects/{pid}/export")
    async def export(pid: str, o: str = Depends(owner)):
        data = svc.export(o, pid_ok(pid))
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{pid}.zip"'})

    @app.post("/api/import")
    async def import_(req: Request, o: str = Depends(owner)):
        data = await req.body()
        return await act(svc.import_archive, o, data)

    @app.get("/api/projects/{pid}/trace")
    async def trace(pid: str, o: str = Depends(owner)):
        svc.state(o, pid_ok(pid))
        p = svc.store.runs_dir(pid) / "trace.jsonl"
        lines = p.read_text().splitlines()[-300:] if p.is_file() else []
        return [json.loads(x) for x in lines]

    @app.get("/api/projects/{pid}/events")
    async def events(pid: str, o: str = Depends(owner)):
        svc.state(o, pid_ok(pid))
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        sink = lambda ev: loop.call_soon_threadsafe(q.put_nowait, ev)
        svc.sinks.setdefault(pid, []).append(sink)

        async def gen():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), 15)
                        yield f"data: {json.dumps(ev)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                if sink in svc.sinks.get(pid, []):
                    svc.sinks[pid].remove(sink)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/")
    async def index():
        return FileResponse(UI / "index.html")

    @app.get("/static/{name}")
    async def static(name: str):
        if not re.match(r"^[a-z0-9_.-]+$", name) or not (UI / name).is_file():
            raise HTTPException(404)
        return FileResponse(UI / name)

    app.state.service = svc
    return app


def main() -> None:  # python -m appbuilder.server
    import os
    import sys
    import uvicorn
    s = Settings.from_env()
    if not s.app_password:
        print("WARNING: APP_PASSWORD is not set. Anyone who can reach this server can use it, spend your model quota and run "
              "model-written code on this machine.", file=sys.stderr, flush=True)
    uvicorn.run(create_app(s), host="0.0.0.0", port=int(os.environ.get("PORT") or s.api_port), log_level="warning")


if __name__ == "__main__":
    main()
