"""SUPPLIED. The trusted check worker. Runs in its own process with a scrubbed environment.

It serves the project's ``app/`` folder, opens each criterion in a fresh browser context (clean
storage), interprets the structured steps, and prints one JSON line per criterion. It contains no
model code and receives no credentials.

Execution boundaries enforced here (each has a test in tests/test_boundaries.py):
  * every request that is not to the app's own origin is aborted and recorded (data:, blob:, about:
    do not leave the browser and are allowed);
  * WebSockets are mocked and never connected, service workers are blocked, downloads and permissions are off;
  * each criterion gets a new context, so storage never carries over between criteria;
  * the process environment holds no API keys (the parent builds it from scratch).
Known limit: Chromium is first launched with its own OS sandbox; where that cannot start (as root, in most containers) it is
relaunched with ``--no-sandbox`` and the report says so under ``conditions.chromium_sandbox``: false. Hosting should add
container isolation.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


class StepFailed(Exception):
    """The app did not meet the step (a requirement is not satisfied). Reported as 'failed'."""


def launch_browser(pw):
    """Launch headless Chromium with its OS sandbox; if that fails, retry with ``--no-sandbox``.
    Returns ``(browser, sandbox_used)``; the second launch's error is raised if it fails too."""
    from playwright.sync_api import Error as PWError

    args = ["--disable-dev-shm-usage"]
    try:
        return pw.chromium.launch(headless=True, args=args, chromium_sandbox=True), True
    except PWError:
        return pw.chromium.launch(headless=True, args=args + ["--no-sandbox"], chromium_sandbox=False), False


def main() -> None:
    job = json.loads(sys.stdin.read())
    sys.path.insert(0, job["package_root"])
    from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout, sync_playwright

    from appbuilder.supplied.preview import serve_directory

    timeout_ms = int(job["step_timeout_ms"])
    httpd, origin = serve_directory(Path(job["app_dir"]), label="check", with_csp=job.get("with_csp", True))
    blocked: list[dict] = []
    diagnostics: list[str] = []

    def route_handler(route):
        url = route.request.url
        if url == origin or url.startswith(origin + "/") or url.startswith(("data:", "blob:", "about:")):
            route.continue_()
        else:
            blocked.append({"url": url[:200], "resource_type": route.request.resource_type})
            route.abort()

    def loc(page, target):
        return page.locator(f'[data-testid="{target}"]')

    def poll(page, fn, expected_desc):
        import time
        deadline = time.monotonic() + timeout_ms / 1000
        last = None
        while True:
            ok, last = fn()
            if ok:
                return
            if time.monotonic() > deadline:
                raise StepFailed(f"{expected_desc}; found {last}")
            page.wait_for_timeout(50)

    def run_step(page, s):
        a = s["action"]
        if a == "navigate":
            r = page.goto(f"{origin}/{s['page']}", wait_until="load")
            if r is None or r.status != 200:
                raise StepFailed(f"page {s['page']} could not be opened (HTTP {r.status if r else 'none'})")
        elif a == "reload":
            page.reload(wait_until="load")
        elif a in ("enter", "select", "filter"):
            el = loc(page, s["target"])
            tag = el.evaluate("e => e.tagName")
            if tag == "SELECT":
                el.select_option(s["value"])
            elif a == "select":
                raise StepFailed(f"[{s['target']}] is not a dropdown")
            else:
                el.fill(s["value"])
                if a == "filter":
                    el.dispatch_event("change")
        elif a == "click":
            loc(page, s["target"]).click()
        elif a == "assert_text":
            el = loc(page, s["target"])
            want = s.get("equals", s.get("contains"))

            def check():
                txt = (el.inner_text() or "").strip()
                return ((txt == want) if "equals" in s else (want in txt)), repr(txt)

            poll(page, check, f"expected [{s['target']}] to {'read' if 'equals' in s else 'contain'} {want!r}")
        elif a == "assert_count":
            el = loc(page, s["target"])
            poll(page, lambda: (el.count() == s["count"], el.count()), f"expected {s['count']} element(s) [{s['target']}]")
        elif a == "assert_visible":
            el = loc(page, s["target"])
            poll(page, lambda: (el.count() >= 1 and el.first.is_visible(), f"{el.count()} element(s)"),
                 f"expected [{s['target']}] to be visible")
        else:  # the parent validated the steps; defence in depth
            raise StepFailed(f"unsupported step {a!r}")

    with sync_playwright() as pw:
        browser, sandbox = launch_browser(pw)
        emit({"type": "conditions", "browser": f"chromium {browser.version}", "chromium_sandbox": sandbox,
              "worker_env_keys": sorted(os.environ), "fixed_time": job.get("fixed_time")})
        for c in job["criteria"]:
            res = {"type": "result", "id": c["id"], "status": "errored", "failed_step": None, "message": ""}
            context = browser.new_context(service_workers="block", accept_downloads=False,
                                          viewport={"width": 1024, "height": 768})
            try:
                context.route("**/*", route_handler)
                # A WebSocketRoute whose handler never calls connect_to_server() is mocked: the page's socket
                # never reaches the network. (Calling ws.close() inside the handler deadlocks Playwright's sync
                # API - found by tests/test_boundaries.py - so the attempt is recorded and left unconnected.)
                context.route_web_socket("**/*", lambda ws: blocked.append(
                    {"url": ws.url[:200], "resource_type": "websocket"}))
                if job.get("fixed_time"):
                    context.clock.set_fixed_time(job["fixed_time"])
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
                page.on("pageerror", lambda e, cid=c["id"]: diagnostics.append(f"{cid}: page error: {str(e)[:160]}"))
                page.on("console", lambda m, cid=c["id"]: diagnostics.append(f"{cid}: console.error: {m.text[:160]}")
                        if m.type == "error" else None)
                for i, s in enumerate(c["steps"]):
                    try:
                        run_step(page, s)
                    except StepFailed as e:
                        res.update(status="failed", failed_step=i + 1, message=str(e))
                        break
                    except PWTimeout:
                        t = s.get("target", s.get("page", ""))
                        res.update(status="failed", failed_step=i + 1,
                                   message=f"[{t}] was not found or not usable within {timeout_ms} ms")
                        break
                    except PWError as e:
                        msg = str(e)
                        if "strict mode violation" in msg:
                            res.update(status="failed", failed_step=i + 1,
                                       message=f"more than one element matches [{s.get('target')}]")
                        else:
                            res.update(status="errored", failed_step=i + 1, message=f"{type(e).__name__}: {msg[:200]}")
                        break
                else:
                    res.update(status="passed", message="")
            except Exception as e:  # harness-level problem, not an app failure
                res.update(status="errored", message=f"{type(e).__name__}: {str(e)[:200]}")
            finally:
                try:
                    context.close()
                except Exception:
                    pass
            emit(res)
        browser.close()
    httpd.shutdown()
    emit({"type": "done", "blocked_requests": blocked[:50], "diagnostics": diagnostics[:50]})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - reported to the parent as a harness error
        emit({"type": "fatal", "message": f"{type(exc).__name__}: {str(exc)[:300]}"})
        sys.exit(2)
