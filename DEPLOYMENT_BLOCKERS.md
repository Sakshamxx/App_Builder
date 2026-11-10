# Deployment blockers (what a real public link still needs)

**Status: Prototype—live-model and delivery validation outstanding.** The application can now be deployed as a **temporary,
password-protected demo** (see "Deploy on Render" in `README.md`). That is **not** the same as the requirements below being met: the
list is unchanged and none of it is implemented. This file records requirements, not a chosen design.

What changed: the loopback-only restriction (loopback bind, loopback client and `Host` checks, `tests/test_local_only.py`) was **removed on
purpose** for a short-lived demo. The server now binds `0.0.0.0` on `$PORT`. If `APP_PASSWORD` is set, everything except `/healthz` and
`/preview/<id>/...` needs HTTP Basic auth with that one shared password. Generated apps are served from the same origin at
`/preview/<id>/<file>` with a strict CSP plus a forced CSP `sandbox` (opaque origin). Without `APP_PASSWORD` the server is open to
anyone who can reach it, and prints a warning.

What this still is not: the application runs model-written code. A public address lets whoever knows the password cause that code to run on
the host, the host being a container without operating-system isolation between the server, the worker and the browser. Treat the
deployment as disposable: no real data, a model key you can revoke, delete it afterwards.

## What the current isolation is, and is not

| Mechanism in the prototype | What it does | What it does not establish |
|---|---|---|
| Worker subprocess (`python -I`, scrubbed environment) | keeps the API key and server environment out of the checking process | not OS containment: same OS user, same filesystem and network namespace as the server |
| Password gate (`APP_PASSWORD`, HTTP Basic) | keeps casual visitors out of the builder and the model quota | one shared secret: not identity, not per-project authorisation, no rate limit or lockout, no revocation per person; `/preview/` is deliberately outside the gate |
| Preview served from the main origin with CSP `sandbox` | the page gets an opaque origin, so it cannot read the builder's localStorage owner token or call its API with it, even when opened directly | a browser control only; apps that use `localStorage` do not work in the preview |
| Browser request blocking, CSP `connect-src 'self'`, mocked WebSockets, blocked service workers | stops the *page* reaching other origins in the cases tested | not a statement about the host; it only covers what a page can do in that browser context |
| Per-project preview origin (`p<id>.localhost`) | separates `localStorage` between projects in the browser | not authorisation |
| Timeouts (`check_timeout_s`, step timeouts) | contain a page that never finishes | not CPU, memory, disk or process limits |
| Chromium tried with its own sandbox first, `--no-sandbox` if that cannot start (root, most containers) | lets the checks run in a container | where the fallback happens Chromium's own OS sandbox is off; the report records `chromium_sandbox: false` |

The boundary tests show behaviour for the cases they exercise. They do not show complete containment.

## Separate deployment requirements (none is implemented)

1. **Execution isolation.** Untrusted, model-written HTML/JS must run in an environment with operating-system-level containment (for
   example a separate container or microVM per build with no credentials, no shared filesystem, restricted network, and CPU/memory/
   disk/process limits), assessed on the real hosting platform. Nothing in this repository provides that.
2. **Identity and project authorisation.** A shared access code does **not** stop one learner opening or acting on another learner's
   projects: anyone holding the code (or a guessed or leaked project id plus the browser-held owner token) is treated alike. The current
   owner token is a random browser-held identifier for same-browser saving; it is not authentication and not project-level
   authorisation. Per-learner identity, per-project access rules, and revocation are needed. The options considered earlier
   (including a shared-code-plus-learner-key arrangement, "B+C") are **not** an access-control design. A statement made earlier in
   the planning thread that B+C was sound public access control is withdrawn. No option is selected or implemented.
3. **Credential custody.** Learner-owned model keys do not by themselves authorise a hosted application to collect, store or use them.
   A hosted design has to decide where a key is entered, who can read it, how long it lives, and how it is kept out of logs, exports,
   traces and generated-code execution. The prototype reads a key only from the server's own environment on the learner's machine.
4. **Quotas and abuse limits.** Per-user and per-deployment limits on model calls, builds, checks, storage and import size, with a
   plan for provider rate limits (the free tier is limited) and for who bears any cost.
5. **Data handling.** Retention and deletion of projects, traces and exports; what a learner is told about where their content goes
   (including free-tier terms of the model provider).

## What would have to be true before a public link is considered

Each of 1–5 designed, implemented and tested on the actual hosting platform; the live-model checks run; and an assessment of the
hosting boundary written and reviewed. Until then the only supported way to run the application is on the learner's own computer.
