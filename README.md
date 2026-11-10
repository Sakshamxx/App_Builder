# Project 1 reference application: App Builder Agent

> **Prototype—live-model and delivery validation outstanding.** Local use, or a temporary **password-protected demo** (see "Deploy on Render" and `DEPLOYMENT_BLOCKERS.md`). Not isolated at operating-system level.

An instructor reference implementation of the Project 1 app: describe a small app in plain language, approve a scope and
acceptance criteria, and an agent builds a browser-only app (up to three linked pages) that a trusted harness then checks.
Two implementations of the agent loop share the same behavioural requirements: a plain-Python loop (`appbuilder/agent/loop.py`,
the course reference) and a LangGraph version (`appbuilder/framework/graph.py`).

**Status: runnable locally and exercised against a scripted stub model, hand-written apps in a real browser, and a local fake provider.
No live model has been run** (no key was authorised); see `VALIDATION.md` for exactly what is and is not demonstrated. No public link
exists yet. The results in `VALIDATION.md` were produced from the build copy in the authoring container. The copy placed in the course
folder was integrity-checked by checksum (that shows the files transferred intact, nothing more) and **has not been run there**.

**Deployable as a password-protected demo, still not isolated.** The earlier loopback-only restriction has been removed: the server binds
`0.0.0.0` on `$PORT` (default 8080), and if `APP_PASSWORD` is set everything except `/healthz` and `/preview/` needs HTTP Basic auth (any
username). The password is one shared secret, not per-user authorisation. The app still runs model-written code; a separate worker process,
browser request blocking, a CSP sandbox on previews and timeouts are **not operating-system containment**, and the Chromium sandbox is
usually off in a container (reports record `chromium_sandbox`). Use a throw-away deployment, a key you can revoke, and delete it afterwards.
The remaining requirements in `DEPLOYMENT_BLOCKERS.md` are unmet.

## Run it (demo mode, no key)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium            # the trusted harness and preview checks need it
cp .env.example .env && set -a && source .env && set +a      # defaults to demo mode
python -m appbuilder.server            # UI at http://localhost:8080  (generated apps at http://localhost:8080/preview/<project id>/index.html)
```

The server listens on all network interfaces. Set `APP_PASSWORD` in `.env` (or export it) to require a password; without it a warning is
printed and anyone who can reach the port can use the app. `PORT` overrides the port (default 8080).

Generated apps are shown in an iframe from `/preview/<id>/` with `sandbox="allow-scripts allow-forms"` and a CSP `sandbox`. The page gets an opaque
origin so it cannot reach the builder's origin, its storage or its API token, and the consequence is that **an app that uses `localStorage` throws a
`SecurityError` in the preview** (the trusted checks run it in their own origin and are unaffected). To use a generated app for real, export it.

Demo mode (`APPBUILDER_PROVIDER=demo`) is a labelled scripted stand-in that knows three examples (tip calculator, habit tracker,
expense tracker). It is not a model. Try "a tip calculator", then "an online shop with payments" (shows the unsupported path).

## Run it with a model

1. **Gemini (primary direction, learner-owned key).** Create a key in Google AI Studio, then in `.env` set `APPBUILDER_PROVIDER=gemini`,
   `APPBUILDER_MODEL=<model id>` and `GEMINI_API_KEY=<key>`. The key stays server-side: it is read once into the transport, sent only in
   the Authorization header, never passed to the harness process, exports, browser storage, logs or traces. Free-tier terms (including that
   free-tier content may be used to improve Google products) are the learner's to review before use.
2. **Local fallback.** `APPBUILDER_PROVIDER=ollama`, `APPBUILDER_MODEL=<pulled model that supports tool calling>`.
3. Run the three live checks first: `APPBUILDER_LIVE=1 python -m pytest tests/test_live.py -v`. They record what your provider actually
   returns (tool-call shape, streamed tool-call deltas, usage fields) in `evidence/live-*.json`. Fix `model_transport.py` if the provider differs.
4. Switch loops with `APPBUILDER_IMPL=framework`.

## Deploy on Render (temporary demo, free plan)

1. Push this folder to a GitHub repository. Never commit `.env` (it is in `.gitignore` and `.dockerignore`).
2. In Render choose **New > Web Service**, connect the repository, set **Runtime: Docker** and **Instance type: Free**. Render builds the `Dockerfile` and
   passes `PORT`; set the **Health Check Path** to `/healthz`.
3. Under **Environment** add: `GEMINI_API_KEY`, `APPBUILDER_PROVIDER=gemini`, `APPBUILDER_MODEL=<a model id your key can use>` and `APP_PASSWORD=<a long password>`.
4. Open the service URL; the browser asks for a username (anything) and the password.

Things to expect on the free plan: the service sleeps after idle time and the first request is slow; the disk is ephemeral, so projects are lost on every
restart or redeploy; memory is small (about 512 MB) and a Chromium check may run out of it; the Gemini free tier has its own request limits.
When you are done, delete the Render service and revoke or rotate the Gemini key.

## Tests and validation

```bash
python -m pytest                 # several minutes (real Chromium); the live tests skip without a key
python scripts/run_validation.py # re-runs everything; VALIDATION.md reports collected/passed/failed/skipped per evidence category
```

## Layout

```
appbuilder/
  config.py            every limit and setting in one place (caps: 20 steps, 3 repairs, 6 tool calls/turn, ...)
  agent/               LEARNER-OWNED: loop, tools+dispatch, prompts, contracts, structured output, scope, criteria,
                       revision judgment, retry, usage/cost, trace, status, workflow service
  framework/graph.py   LEARNER-OWNED: the same behaviour in LangGraph
  supplied/            SUPPLIED: workspace, store, criteria vocabulary, preview server, harness + worker, restore/promotion,
                       regression planning, export/import, model transport, redaction, server, UI
  testing/             scripted stand-in model and demo transport (test/demo only)
tests/                 boundary, harness, services, scenarios (both implementations), server, UI, live
05_projects/... evidence/ screenshots and junit XML       VALIDATION.md   DECISIONS.md   COMPONENT_MAP.md
```

## How a build works (read `agent/loop.py`)

request -> **scope assessment** (structured model call; unsupported parts explained, alternatives need approval) -> user approves scope ->
**criteria** in a bounded vocabulary (validated; what cannot be checked is listed as *unresolved*, and nothing can be approved or built until the user
chooses, for each one, a supported replacement or to remove it from scope) -> user approves the criteria **as plain-language behaviour** (bound to a hash
of what they saw; test ids stay internal) -> **build loop** (model calls tools: list/read/write/delete files, `run_checks`, `run_diagnostic`) -> stop rules -> status derived by
code from the last trusted report for the current files and criteria. Revisions are judged against the approved requirements: compatible
changes re-run every criterion, conflicts stop and ask, unsupported changes are declined. One last-working restore point is kept and only a
passing trusted report for the current files can replace it.

Statuses: `verified` means "passed the listed checks under these conditions" (browser, clock, clean storage, network limited to the app's origin).
It does not mean the app is correct beyond the approved criteria.

## Known limits

See "Limitations" in `VALIDATION.md`, `DECISIONS.md` and `DEPLOYMENT_BLOCKERS.md`: no OS-level isolation of generated code (and Chromium's own
sandbox is off when run as root), the owner token is not authentication or project authorisation, no live model has been run, six-session
feasibility is unvalidated, and the hosting route is unresolved.

## What "verified" does and does not mean

`verified` = passed the listed checks under the stated conditions. The checks are the approved examples. Where an outcome depends on user
input, proposals must include at least two examples with different inputs and results (a rule the criteria step enforces), so a page that only
reproduces one example fails. This reduces, and does not eliminate, special-casing of the listed examples. The live acceptance checks are not
the protected course assessment: any assessment that must resist gaming needs inputs the learner and model never see, held separately.
