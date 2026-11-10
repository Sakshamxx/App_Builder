"""The real browser UI (Chromium) against the real server in DEMO mode: create, approve, build, preview, reopen after a reload,
export, import into a fresh browser profile, restore. Screenshots are written to evidence/ for the validation report."""
import os
import re
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from appbuilder.config import Settings
from appbuilder.server import create_app

EVID = Path(__file__).resolve().parents[1] / "evidence"


def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture
def ui(tmp_path):
    api, prev = free(), free()
    st = Settings(data_dir=tmp_path / "d", provider="demo", model="demo-stub", api_port=api, preview_port=prev)
    srv = uvicorn.Server(uvicorn.Config(create_app(st), host="127.0.0.1", port=api, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{api}/api/config", timeout=1); break
        except Exception:
            time.sleep(0.1)
    yield f"http://localhost:{api}", tmp_path
    srv.should_exit = True; t.join(5)


def test_user_journey_in_a_real_browser(ui):
    base, tmp = ui
    EVID.mkdir(exist_ok=True)
    args = ["--no-sandbox"] if os.geteuid() == 0 else []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=args)
        ctx = b.new_context(accept_downloads=True, viewport={"width": 1100, "height": 1000})
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(base)
        expect(pg.locator("#banner")).to_contain_text("DEMO MODE")
        pg.fill("#request", "A tip calculator that splits a bill and remembers my last tip")
        pg.click("#createBtn")
        expect(pg.get_by_text("Review the proposed scope")).to_be_visible(timeout=30000)
        pg.screenshot(path=str(EVID / "ui-1-scope-approval.png"))
        pg.get_by_role("button", name="Approve").click()
        expect(pg.get_by_text("Review the acceptance criteria")).to_be_visible(timeout=30000)
        expect(pg.locator("pre").nth(0)).to_contain_text("C1:")
        pg.screenshot(path=str(EVID / "ui-2-criteria-approval.png"))
        pg.get_by_role("button", name="Approve").click()
        expect(pg.locator(".badge.s-verified").first).to_be_visible(timeout=120000)
        expect(pg.get_by_text("passed the listed checks under these conditions")).to_be_visible()
        # the preview is the main app's own /preview/<id>/ path, in an iframe sandboxed WITHOUT allow-same-origin. The page renders there,
        # but the sandbox gives it an opaque origin, so an app that touches localStorage (this demo app does, at load) throws a
        # SecurityError and its script does not run. What the app does is decided by the trusted checks (status above), not by this iframe.
        frame_el = pg.locator("iframe")
        expect(frame_el).to_have_attribute("sandbox", "allow-scripts allow-forms")
        expect(frame_el).to_have_attribute("src", re.compile(r"^/preview/p[0-9a-f]{20}/index\.html$"))
        f = pg.frame_locator("iframe")
        expect(f.locator("h1")).to_have_text("Tip calculator")
        expect(f.locator('[data-testid="calculate"]')).to_be_visible()
        pg.screenshot(path=str(EVID / "ui-3-verified-preview.png"))
        # same-browser save/reopen
        pg.reload()
        expect(pg.locator("#projects li")).to_have_count(1)
        pg.locator("#projects li").first.click()
        expect(pg.locator(".badge.s-verified").first).to_be_visible()
        # export, then import in a brand-new browser profile (a different owner token)
        with pg.expect_download() as d:
            pg.get_by_role("button", name="Export (.zip)").click()
        zip_path = tmp / "export.zip"
        d.value.save_as(zip_path)
        ctx2 = b.new_context(viewport={"width": 1100, "height": 1000})
        p2 = ctx2.new_page()
        p2.goto(base)
        expect(p2.get_by_text("No projects yet")).to_be_visible()
        p2.set_input_files("#importFile", str(zip_path))
        expect(p2.get_by_text("IMPORTED, NOT TRUSTED", exact=False)).to_be_visible(timeout=30000)
        p2.screenshot(path=str(EVID / "ui-4-import-untrusted.png"))
        p2.get_by_role("button", name="Approve").click()
        expect(p2.locator(".badge.s-verified").first).to_be_visible(timeout=120000)
        # unsupported request in demo mode shows the explanation and an alternative button
        pg.fill("#request", "An online shop with payments and user logins")
        pg.click("#createBtn")
        expect(pg.get_by_text("This request is not supported")).to_be_visible(timeout=30000)
        expect(pg.get_by_role("button", name="Use the suggested alternative")).to_be_visible()
        pg.screenshot(path=str(EVID / "ui-5-unsupported.png"))
        b.close()
    # the one expected browser error: the sandboxed preview blocks localStorage (see the preview comment above)
    assert not [e for e in errors if "favicon" not in e and "lacks the 'allow-same-origin' flag" not in e], errors
