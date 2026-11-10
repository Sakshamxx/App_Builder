"""Hosted-demo behaviour: password gate, /healthz, /preview/<id>/<file>, port and host settings, Chromium sandbox fallback.

What this proves: the HTTP behaviour of the app as deployed (real uvicorn, raw request paths).
What it does NOT prove: any containment of generated code. The password is one shared secret, not per-user authorisation, and the
preview CSP sandbox is a browser control, not operating-system isolation (see DEPLOYMENT_BLOCKERS.md).
"""
import base64
import http.client
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Error as PWError

from appbuilder import server
from appbuilder.config import Settings
from appbuilder.server import create_app
from appbuilder.supplied.harness_worker import launch_browser

PW = "correct horse battery"


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def basic(pw, user="anyone"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


class Site:
    def __init__(self, port, store, pid):
        self.port, self.store, self.pid = port, store, pid

    def req(self, path, method="GET", headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, data

    def pv(self, rest, **kw):
        return self.req(f"/preview/{self.pid}{rest}", **kw)


@pytest.fixture
def site(tmp_path):
    started = []

    def start(password=None, **extra):
        port = free()
        st = Settings(data_dir=tmp_path / f"d{len(started)}", provider="demo", model="demo-stub", api_port=port,
                      app_password=password, **extra)
        app = create_app(st)
        srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        t = threading.Thread(target=srv.run, daemon=True); t.start()
        for _ in range(200):
            if srv.started:
                break
            time.sleep(0.05)
        started.append((srv, t))
        store = app.state.service.store
        pid = store.create("owner-a-0123456789", "A")
        app_dir = store.app_dir(pid)
        (app_dir / "index.html").write_text("<p data-testid='m'>AAA</p><a href='two.html'>2</a>")
        (app_dir / "two.html").write_text("<p>TWO</p>")
        (app_dir / "style.css").write_text("body{}")
        (app_dir / "app.js").write_text("1")
        (store.dir(pid) / "secret.txt").write_text("TOP-SECRET")
        return Site(port, store, pid)

    yield start
    for srv, t in started:
        srv.should_exit = True; t.join(5)


# ---- /healthz and the password gate -----------------------------------------------------------------
@pytest.mark.parametrize("password", [None, PW])
def test_healthz_is_open_and_reports_ok(site, password):
    st, h, body = site(password).req("/healthz")
    assert st == 200 and json.loads(body) == {"ok": True}


def test_without_app_password_everything_is_open(site):
    s = site()
    assert s.req("/")[0] == 200 and s.req("/api/config")[0] == 200


@pytest.mark.parametrize("path,method", [("/", "GET"), ("/api/config", "GET"), ("/api/projects", "GET"), ("/static/app.js", "GET"),
                                         ("/api/projects", "POST"), ("/api/import", "POST"), ("/healthz/", "GET"), ("/preview", "GET")])
def test_password_gate_blocks_ui_and_api_without_credentials(site, path, method):
    st, h, _ = site(PW).req(path, method, body=b"{}" if method == "POST" else None)
    assert st == 401 and h["www-authenticate"].startswith("Basic")


@pytest.mark.parametrize("hdr", [basic("wrong"), basic(""), basic(PW + "x"), basic(PW[:-1]), {"Authorization": "Basic !!!notbase64"},
                                 {"Authorization": "Basic"}, {"Authorization": "Bearer " + PW},
                                 {"Authorization": "Basic " + base64.b64encode(PW.encode()).decode()}])   # no "user:" part
def test_wrong_or_malformed_credentials_are_refused(site, hdr):
    assert site(PW).req("/api/config", headers=hdr)[0] == 401


@pytest.mark.parametrize("user", ["anyone", "", "admin", "söme user"])
def test_any_username_works_with_the_right_password(site, user):
    s = site(PW)
    assert s.req("/api/config", headers=basic(PW, user))[0] == 200 and s.req("/", headers=basic(PW, user))[0] == 200


def test_a_password_containing_colons_works(site):
    s = site("a:b:c-secret")
    assert s.req("/api/config", headers=basic("a:b:c-secret"))[0] == 200 and s.req("/api/config", headers=basic("a"))[0] == 401


def test_the_gate_cannot_be_bypassed_through_the_preview_prefix(site):
    s = site(PW)
    for path in ("/preview/../api/config", f"/preview/{s.pid}/../../api/config", f"/preview/{s.pid}/%2e%2e/%2e%2e/api/config",
                 "/preview/x/../api/projects", "//api/config", "/healthz/../api/config"):
        st, _, body = s.req(path)
        assert st in (401, 404) and b'"provider"' not in body, path


def test_the_password_is_redacted_and_never_shown():
    st = Settings.from_env({"APP_PASSWORD": "s3cret-demo-pass", "APPBUILDER_PROVIDER": "demo"})
    assert st.app_password == "s3cret-demo-pass" and "s3cret" not in repr(st) and "s3cret-demo-pass" in st.secrets()
    assert Settings.from_env({}).app_password is None and Settings.from_env({"APP_PASSWORD": ""}).app_password is None


# ---- /preview ---------------------------------------------------------------------------------------
def test_preview_needs_no_password_and_serves_the_project_files(site):
    s = site(PW)
    for rest, needle, ctype in (("/index.html", b"AAA", "text/html"), ("/", b"AAA", "text/html"), ("/two.html", b"TWO", "text/html"),
                                ("/style.css", b"body", "text/css"), ("/app.js", b"1", "text/javascript")):
        st, h, body = s.pv(rest)
        assert st == 200 and needle in body and h["content-type"].startswith(ctype), rest


def test_preview_keeps_the_strict_csp_and_forces_a_sandbox(site):
    st, h, _ = site(PW).pv("/index.html")
    pol = h["content-security-policy"]
    assert "default-src 'self'" in pol and "connect-src 'self'" in pol and "object-src 'none'" in pol
    assert "frame-ancestors 'self'" in pol and "frame-src" not in pol
    assert pol.endswith("sandbox allow-scripts allow-forms") and "allow-same-origin" not in pol
    assert h["x-content-type-options"] == "nosniff" and h["cache-control"] == "no-store"


def test_the_builder_ui_policy_allows_framing_only_itself(site):
    st, h, _ = site().req("/")
    pol = h["content-security-policy"]
    assert "frame-src 'self'" in pol and "localhost" not in pol and "frame-ancestors 'none'" in pol


@pytest.mark.parametrize("rest", ["/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/%2e%2e%2fsecret.txt",
                                  "/../../etc/passwd", "//etc/passwd", "/.hidden", "/a/b.html", "/secret.txt", "/..\\secret.txt",
                                  "/index.html%00.txt", "/meta.json", "/approved/criteria.json", "/../app/index.html", "/app/index.html",
                                  "/index.html/", "/INDEX.HTM", "/missing.html"])
def test_preview_path_traversal_and_non_app_files_are_404(site, rest):
    st, _, body = site(PW).pv(rest)
    assert st == 404 and b"TOP-SECRET" not in body and b"root:" not in body and b"AAA" not in body


@pytest.mark.parametrize("pid", ["pffffffffffffffffffff", "p123", "P0123456789abcdef0123", "pg0123456789abcdef012", "..", "%2e%2e",
                                 "p0123456789abcdef01234", "", "api"])
def test_unknown_or_malformed_project_ids_are_404(site, pid):
    s = site()                                   # no password, so the answer comes from the route itself
    for rest in ("/index.html", "/", "/style.css"):
        st, _, body = s.req(f"/preview/{pid}{rest}")
        assert st == 404 and b"AAA" not in body, (pid, rest)


def test_a_wellformed_but_unknown_id_is_404_not_401(site):
    s = site(PW)
    assert s.req("/preview/pffffffffffffffffffff/index.html")[0] == 404 and s.req("/preview/pffffffffffffffffffff/")[0] == 404


def test_preview_is_read_only_and_serves_no_symlinks(site):
    s = site(PW)
    for m in ("POST", "PUT", "DELETE", "PATCH"):
        assert s.pv("/index.html", method=m, body=b"x")[0] == 405
    os.symlink(s.store.dir(s.pid) / "secret.txt", s.store.app_dir(s.pid) / "leak.html")
    st, _, body = s.pv("/leak.html")
    assert st == 404 and b"TOP-SECRET" not in body


def test_preview_serves_the_latest_files(site):
    s = site(PW)
    (s.store.app_dir(s.pid) / "index.html").write_text("<p>NEW</p>")
    assert b"NEW" in s.pv("/index.html")[2]


def test_the_separate_preview_server_is_not_started(site):
    port = free()
    site(PW, preview_port=port)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)
    assert not hasattr(server, "PreviewServer")


def test_the_ui_embeds_the_preview_from_the_main_app_in_a_sandbox():
    js = (Path(server.__file__).parent / "supplied" / "ui" / "app.js").read_text()
    assert "`/preview/${p.id}/${first}`" in js and 'sandbox: "allow-scripts allow-forms"' in js
    assert "localhost" not in js and "allow-same-origin" not in js and "preview_port" not in js


# ---- how the server starts ----------------------------------------------------------------------------
@pytest.fixture
def started(monkeypatch):
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(kw))
    for k in ("PORT", "APPBUILDER_API_PORT", "APPBUILDER_HOST", "APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("sys.argv", ["server"])
    return seen


def test_main_binds_all_interfaces_on_the_port_from_the_environment(started, monkeypatch):
    monkeypatch.setenv("PORT", "10000")
    server.main()
    assert started["host"] == "0.0.0.0" and started["port"] == 10000


def test_main_defaults_to_port_8080(started):
    server.main()
    assert started["host"] == "0.0.0.0" and started["port"] == 8080


def test_main_no_longer_refuses_a_host_setting(started, monkeypatch):
    monkeypatch.setenv("APPBUILDER_HOST", "0.0.0.0")
    monkeypatch.setattr("sys.argv", ["server", "--host", "0.0.0.0"])
    server.main()
    assert started["host"] == "0.0.0.0"


def test_main_warns_when_there_is_no_password_and_stays_quiet_with_one(started, monkeypatch, capsys):
    server.main()
    assert "APP_PASSWORD is not set" in capsys.readouterr().err
    monkeypatch.setenv("APP_PASSWORD", "a-demo-password")
    server.main()
    assert capsys.readouterr().err == ""


# ---- Chromium sandbox fallback --------------------------------------------------------------------------
class FakeChromium:
    def __init__(self, sandbox_works):
        self.sandbox_works, self.calls = sandbox_works, []

    def launch(self, **kw):
        self.calls.append(kw)
        if kw.get("chromium_sandbox") and not self.sandbox_works:
            raise PWError("No usable sandbox! Running as root without --no-sandbox is not supported.")
        if not kw.get("chromium_sandbox") and "--no-sandbox" not in kw["args"] and self.sandbox_works == "never":
            raise PWError("cannot start")
        return "browser"


class FakePW:
    def __init__(self, chromium):
        self.chromium = chromium


def test_chromium_is_launched_with_its_sandbox_when_that_works():
    c = FakeChromium(True)
    assert launch_browser(FakePW(c)) == ("browser", True)
    assert len(c.calls) == 1 and c.calls[0]["chromium_sandbox"] is True and "--no-sandbox" not in c.calls[0]["args"]


def test_chromium_falls_back_to_no_sandbox_and_says_so():
    c = FakeChromium(False)
    assert launch_browser(FakePW(c)) == ("browser", False)
    assert len(c.calls) == 2 and c.calls[1]["chromium_sandbox"] is False and "--no-sandbox" in c.calls[1]["args"]


def test_a_launch_that_fails_both_ways_is_raised_not_hidden():
    class Broken:
        def launch(self, **kw):
            raise PWError("cannot start")
    with pytest.raises(PWError):
        launch_browser(FakePW(Broken()))


def test_the_report_records_which_chromium_mode_ran(tmp_path):
    from appbuilder.config import Limits
    from appbuilder.supplied.harness import run_checks
    app = tmp_path / "app"; app.mkdir()
    (app / "index.html").write_text("<p data-testid='x'>hi</p>")
    crit = {"criteria": [{"id": "C1", "title": "t", "requirement_ids": ["R1"],
                          "steps": [{"action": "navigate", "page": "index.html"}, {"action": "assert_text", "target": "x", "equals": "hi"}]}]}
    rep = run_checks(app, crit, Limits())
    assert rep["all_passed"] and rep["conditions"]["chromium_sandbox"] in (True, False)
