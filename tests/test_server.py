"""HTTP layer: owner scoping, no key leakage, actions, SSE, export/import, UI served with a strict CSP. Demo transport, real harness."""
import io
import json
import threading
import time
import zipfile

import httpx
import pytest
import uvicorn

from appbuilder.config import Limits, Settings
from appbuilder.server import create_app

OWNER, OWNER2 = "owner-aaaaaaaaaaaaaaaa", "owner-bbbbbbbbbbbbbbbb"
KEY = "AIzaSyCANARYcanaryCANARY0123456789"


@pytest.fixture
def server(tmp_path):
    import socket
    def free():
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
    api, prev = free(), free()
    st = Settings(data_dir=tmp_path / "d", provider="demo", model="demo-stub", api_key=KEY, api_port=api, preview_port=prev)
    app = create_app(st)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=api, log_level="error")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{api}/api/config", timeout=1); break
        except Exception:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{api}", prev, st
    srv.should_exit = True; t.join(5)


def H(o=OWNER):
    return {"X-Owner-Token": o}


def test_config_never_includes_the_key_and_ui_has_strict_csp(server):
    base, prev, st = server
    r = httpx.get(base + "/api/config")
    assert KEY not in r.text and r.json()["demo"] is True
    ui = httpx.get(base + "/")
    assert "script-src 'self'" in ui.headers["content-security-policy"] and "'unsafe-inline'" not in ui.headers["content-security-policy"].split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in ui.headers["content-security-policy"] and "App Builder Agent" in ui.text
    assert httpx.get(base + "/static/app.js").status_code == 200 and httpx.get(base + "/static/..%2fserver.py").status_code in (404, 400)


def test_requests_need_an_owner_token_and_projects_are_private(server):
    base, *_ = server
    assert httpx.get(base + "/api/projects").status_code == 401
    assert httpx.get(base + "/api/projects", headers={"X-Owner-Token": "short"}).status_code == 401
    r = httpx.post(base + "/api/projects", headers=H(), json={"request": "a tip calculator"}, timeout=60)
    pid = r.json()["id"]
    assert httpx.get(f"{base}/api/projects/{pid}", headers=H(OWNER2)).status_code == 404
    assert httpx.get(f"{base}/api/projects/{pid}/export", headers=H(OWNER2)).status_code == 404
    assert httpx.get(f"{base}/api/projects/../../etc/passwd", headers=H()).status_code in (404, 400)
    assert httpx.get(f"{base}/api/projects/pnotvalid", headers=H()).status_code == 404


def test_full_flow_over_http_with_sse_export_import_and_preview(server):
    base, prev, st = server
    pid = httpx.post(base + "/api/projects", headers=H(), json={"request": "A tip calculator"}, timeout=60).json()["id"]
    s = httpx.get(f"{base}/api/projects/{pid}", headers=H()).json()
    assert s["status"]["status"] == "awaiting_scope_approval"
    events = []
    def listen():
        with httpx.stream("GET", f"{base}/api/projects/{pid}/events", headers=H(), timeout=120) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
                    if events[-1]["type"] == "build_end":
                        return
    th = threading.Thread(target=listen, daemon=True); th.start(); time.sleep(0.5)
    s = httpx.post(f"{base}/api/projects/{pid}/approve-scope", headers=H(), json={"hash": s["pending"]["scope_hash"]}, timeout=60).json()
    assert s["status"]["status"] == "awaiting_criteria_approval" and "Tip amount" in s["pending"]["readable"] and "tip-amount" not in s["pending"]["readable"]
    assert httpx.post(f"{base}/api/projects/{pid}/approve-criteria", headers=H(), json={"hash": "0" * 64}).status_code == 400
    s = httpx.post(f"{base}/api/projects/{pid}/approve-criteria", headers=H(), json={"hash": s["pending"]["criteria_hash"]}, timeout=120).json()
    th.join(10)
    assert s["status"]["status"] == "verified" and s["outcome"]["stop_reason"] == "checks_passed"
    types = [e["type"] for e in events]
    assert "model_text" in types and "check_report" in types and types[-1] == "build_end"
    # the generated app is served by the main app at /preview/<id>/<file>, with the strict CSP and a forced sandbox
    pr = httpx.get(f"{base}/preview/{pid}/index.html")
    pol = pr.headers["content-security-policy"]
    assert pr.status_code == 200 and "connect-src 'self'" in pol and "frame-ancestors 'self'" in pol and pol.endswith("sandbox allow-scripts allow-forms")
    # export has no owner token/key; import by another owner is untrusted
    z = httpx.get(f"{base}/api/projects/{pid}/export", headers=H()).content
    assert OWNER.encode() not in b"".join(zipfile.ZipFile(io.BytesIO(z)).read(n) for n in zipfile.ZipFile(io.BytesIO(z)).namelist())
    imp = httpx.post(base + "/api/import", headers=H(OWNER2), content=z, timeout=60).json()
    assert imp["status"]["status"] == "awaiting_criteria_approval" and imp["id"] != pid
    done = httpx.post(f"{base}/api/projects/{imp['id']}/approve-criteria", headers=H(OWNER2), json={"hash": imp["pending"]["criteria_hash"]}, timeout=120).json()
    assert done["status"]["status"] == "verified"
    assert httpx.post(base + "/api/import", headers=H(OWNER2), content=b"garbage").status_code == 400
    # the key never appears in any response body saved for the project
    assert KEY not in json.dumps(done) and KEY not in httpx.get(f"{base}/api/projects/{pid}/trace", headers=H()).text


def test_unsupported_request_in_demo_mode_and_unknown_action(server):
    base, *_ = server
    s = httpx.post(base + "/api/projects", headers=H(), json={"request": "online shop with payments"}, timeout=60).json()
    assert s["status"]["status"] == "unsupported"
    assert httpx.post(f"{base}/api/projects/{s['id']}/nonsense", headers=H(), json={}).status_code == 404
    assert httpx.post(base + "/api/projects", headers=H(), json={"request": ""}).status_code == 400


def test_resolve_item_action_exists_and_refuses_when_nothing_is_unresolved(server):
    base, *_ = server
    pid = httpx.post(base + "/api/projects", headers=H(), json={"request": "a tip calculator"}, timeout=60).json()["id"]
    r = httpx.post(f"{base}/api/projects/{pid}/resolve-item", headers=H(), json={"requirement_id": "R2", "action": "remove"}, timeout=30)
    assert r.status_code == 400 and "not waiting" in r.json()["error"]
    assert httpx.post(f"{base}/api/projects/{pid}/resolve-item", headers=H(OWNER2), json={"requirement_id": "R2", "action": "remove"}).status_code == 404
