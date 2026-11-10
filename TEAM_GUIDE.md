# Team guide: run it, read it, follow what it is doing

> **Prototype—live-model and delivery validation outstanding.** Local use only. This guide describes the code as it is; it does not add features.

Audience: anyone on the team (instructors, reviewers, engineers) who has to run the Project 1 reference app or explain it to a learner.

---

## 1. What this app is, in one paragraph

A learner types a plain-language request ("a tip calculator"). The app works out what it can build, **asks the user to approve a scope**, turns the scope into **checkable acceptance criteria** and **asks the user to approve those**, then lets an AI agent write a small browser-only app (up to 3 HTML pages). A separate, trusted test runner (a real browser) checks the result against the approved criteria. The app is only called **verified** if every approved check passed for the current files. Everything the AI says is treated as a claim; only the test runner's report counts as evidence.

## 2. Before you run it

| Need | Detail |
|---|---|
| Python | 3.13.18 is what the results were produced with. The team device has 3.10.12 and **the app has never been run there**: treat the first run as a compatibility check and report errors rather than working around them. |
| Disk / network | `pip install` and `playwright install chromium` need internet once. |
| Model key | **Not needed** for demo mode. A Gemini key or a local Ollama model is only needed for live runs, and no live run has been done yet. |
| Where | Run on your own machine only. The server refuses any non-local address by design. |

## 3. Run it (demo mode, no key)

Open a terminal in the `p1-app-builder-agent` folder.

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
set -a && source .env && set +a
python -m appbuilder.server
```

**Windows (Command Prompt)**
```bat
py -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
for /f "usebackq tokens=1,* delims==" %a in (`findstr /v "^#" .env`) do set %a=%b
python -m appbuilder.server
```
(In a `.bat` file double the `%` signs. If the `for` line gives trouble, set the variables by hand: `set APPBUILDER_PROVIDER=demo` and `set APPBUILDER_MODEL=demo-stub`.)

What each step does: creates an isolated Python environment, installs the libraries, downloads the Chromium browser the test runner drives, creates your settings file (demo mode), loads those settings, starts the server.

Then open **http://localhost:8080**. Generated apps open at `http://localhost:8080/preview/<project id>/index.html`. The server listens on every network interface: set `APP_PASSWORD` (any username, that password) if anyone else can reach your machine. To deploy a temporary demo see "Deploy on Render" in `README.md`.

**First walkthrough (about 3 minutes)**
1. Type `a tip calculator` and submit. The status moves to *awaiting scope approval*. Read the scope.
2. Approve the scope. The status moves to *awaiting criteria approval*. The criteria are shown as plain behaviour ("Enter bill 100 and tip 10 percent; the total shows 110"), with no technical selectors.
3. Approve the criteria. The build starts and live events stream into the page.
4. When it finishes, the status is **verified** (with "passed the listed checks under these conditions") and the preview opens.
5. Try `an online shop with payments`. It will be declined as unsupported and an alternative is offered. This is the unsupported path working as designed.

Demo mode is a **scripted stand-in, not a model**. It only knows three examples (tip calculator, habit tracker, expense tracker).

## 4. Run it with a real model (not yet validated)

Edit `.env` (never commit it), then restart.

- **Gemini:** `APPBUILDER_PROVIDER=gemini`, `APPBUILDER_MODEL=<model id your key can use>`, `GEMINI_API_KEY=<your key>`.
- **Ollama (local):** `APPBUILDER_PROVIDER=ollama`, `APPBUILDER_MODEL=<pulled model that supports tool calling>`.
- Before trusting it, run the three live checks: `APPBUILDER_LIVE=1 python -m pytest tests/test_live.py -v`. They record what the provider really returns in `evidence/live-*.json`. If the shapes differ, `appbuilder/supplied/model_transport.py` is the file to adjust.
- Choose the loop with `APPBUILDER_IMPL=handwritten` (default, plain Python) or `APPBUILDER_IMPL=framework` (LangGraph).

Live runs use a paid or quota-limited key. Get explicit approval for use of a key before running.

## 5. Run the tests

```bash
python -m pytest                    # several minutes, uses real Chromium; the 3 live tests skip without a key
python -m pytest tests/test_unresolved.py -v     # one file
python scripts/run_validation.py    # re-runs everything and rewrites VALIDATION.md
```
Last recorded result (container, Python 3.13.18): 241 collected, 238 passed, 0 failed, 3 skipped. Four split runs, with JUnit files in `evidence/`. The full suite can be slow on 2 CPUs; if one run times out, run the test files in groups.

## 6. How a build flows (the story to tell)

```
request
  -> 1 SCOPE ASSESSMENT   model decides: supported / needs clarification / unsupported (+ alternative)
  -> USER APPROVES SCOPE  (approval is bound to a hash of exactly what the user saw)
  -> 2 CRITERIA           model turns requirements into structured checks from a fixed vocabulary
                          requirements that cannot be checked are listed as UNRESOLVED
  -> USER RESOLVES        for each unresolved item: use a supported replacement, or remove from scope (nothing else)
  -> USER APPROVES CRITERIA (shown as plain behaviour; hash-bound)
  -> 3 BUILD LOOP         model calls tools: list/read/write/delete files, run_checks, run_diagnostic
                          stops on: success, 20 model turns, 3 repairs, errors
  -> 4 TRUSTED CHECK      real browser runs every approved criterion
  -> 5 STATUS             derived by code from the last trusted report for the CURRENT files + criteria
  -> 6 RESTORE POINT      only a passing trusted report can replace the single last-working copy
Later: REVISION request -> judged compatible / conflict / unsupported -> user approves -> rebuild -> re-run ALL criteria
```

Two ideas to repeat to anyone new:
- **The model's word is not evidence.** Status, promotion and "verified" come from the harness report, never from model text.
- **Approvals are binding and hash-checked.** If the files or criteria change after a report, the report is stale and status falls back to *unverified*.

## 7. Component by component

Folder: `appbuilder/`. **Learner-owned** = the part learners write in the course. **Supplied** = given to learners as a working service.

### 7.1 Settings: `config.py`
All limits in one place (`Limits`): max 3 pages, 8 files, 64 KB per file, 20 model turns, 3 repairs, 6 tool calls per turn, 12 criteria, 90 s check timeout. `Settings.from_env()` reads provider, model, key, ports and implementation from environment variables. The key is only ever read here and handed to the model transport.

### 7.2 Learner-owned: the agent (`agent/`)

| File | What it does, plainly |
|---|---|
| `loop.py` | **The core agent loop (hand-written).** Sends messages to the model, runs the tools it asks for, counts steps and repairs, stops by the rules, and never treats model claims as proof. Read this first. |
| `tools.py` | The tools the model may call (JSON schemas) and the code that runs them. Bad arguments come back to the model as data, not crashes. Separates the **trusted** check (counts) from a **diagnostic** run (never counts). |
| `prompts.py` | The instructions given to the model for scope, criteria, build and revision judgment. |
| `contracts.py` | Pydantic models for what structured model output must look like (scope result, criteria proposal, unverifiable items, revision verdict). |
| `structured.py` | Calls the model, parses the answer, validates it, and re-asks with feedback if invalid (bounded retries). |
| `scope.py` | Turns a free-form request into supported scope, a clarification question, or "unsupported" with an alternative. |
| `criteria.py` | Turns approved requirements into checks; verifies every requirement is covered; enforces the **varied-example rule** (see 8); lists uncheckable requirements as unresolved. |
| `revision.py` | Judges a change request against approved requirements (compatible / conflict / unsupported) and merges added, replaced or removed criteria. |
| `retry.py` | Retry policy for temporary provider problems (rate limit, timeout) with backoff. Kept separate from "repairs", which are about failing checks. |
| `usage.py` | Records tokens and cost. If the provider reports nothing, it says "unreported"; it never guesses. |
| `trace.py` | Writes every step as one JSON line (secrets masked first) and streams the same events live to the browser. |
| `status.py` | Works out the status from evidence only (list in section 9). |
| `context.py` | Builds the project context the model sees on each turn. |
| `service.py` | **The workflow / orchestrator.** Owns the order of steps, the approval gates, resolving unresolved requirements, revisions, restore, import and the state shown in the UI. Shared by both loop implementations. |

### 7.3 Learner-owned: framework version (`framework/graph.py`)
The same **build/repair loop** written as a LangGraph graph (state, nodes, conditional edges, streaming). Only this loop is graph-based. Scope, criteria, revision judgment and user approvals stay in `service.py` for both versions. There is no LangGraph persistence or interrupt in this prototype, and the tests do not claim one. Taught in provisional Session 5.

### 7.4 Supplied (`supplied/`)

| File | What it does |
|---|---|
| `workspace.py` | The generated app's files, confined to one folder: flat names only, allowed extensions, size and count limits, no path tricks. |
| `store.py` | Project storage on disk, owner tokens, approved contract with its hash, the list of unresolved requirements. Refuses to approve while anything is unresolved. |
| `criteria_vocab.py` | The fixed vocabulary of checks (open page, enter, click, expect text/count…), the validator, and two renderers: `render_readable` (plain behaviour, for users, no selectors) and `render_technical` (with test ids, for the builder). |
| `harness.py` + `harness_worker.py` | The **trusted test runner**. A separate process drives Chromium with a fresh browser context per criterion, clean storage, a fixed clock, requests outside the app origin blocked, WebSockets mocked. It returns the report that decides everything. |
| `preview.py` | The rule for which generated files may be served (flat, allow-listed, no symlinks) and the restrictive content policy. The server uses it for `/preview/<id>/<file>`; the check worker uses its own local copy of the file server. |
| `restore.py` | Keeps one last-working version. Promotion is allowed only for a passing acceptance report whose file hash and criteria hash match the current state, and never while requirements are unresolved. |
| `regression.py` | After a change, labels each check "relevant" or "regression" so failures are explained. |
| `portability.py` | Export a project to a zip (no tokens, keys or logs; unresolved items travel with it) and a hardened import (size, ratio, name and link checks; imported projects are untrusted and must be re-approved and re-checked). |
| `model_transport.py` | The only code that talks HTTP to a model provider (Gemini or Ollama, OpenAI-compatible). Sends one request, returns one reply or a classified error. The key never appears in logs or errors. |
| `redact.py` | Masks secrets before anything is written to a trace. |
| `ui/` | The browser interface: banner, request box, approval cards, unresolved items with "Use this replacement" / "Remove from scope", live event feed, preview. |

### 7.5 Server: `server.py`
FastAPI app. Key routes: `GET/POST /api/projects`, `POST /api/projects/{id}/{action}` (approve-scope, approve-criteria, resolve-item, build, revise, restore…), `GET /api/projects/{id}/events` (live stream), `GET /api/projects/{id}/trace`, export and import. Also `GET /healthz` and `GET /preview/<id>/<file>` (generated apps; outside the password gate). The server binds `0.0.0.0` on `$PORT` (default 8080); if `APP_PASSWORD` is set every other route needs HTTP Basic auth (any username).

### 7.6 Test helpers: `testing/`
`scripted.py` and `demo.py` are the scripted stand-in model used by demo mode and tests. They are not a model and prove nothing about live behaviour.

### 7.7 Tests: `tests/`
| File | Covers |
|---|---|
| `test_boundaries.py` | Secrets stay out of exports, logs and the harness; confinement rules. |
| `test_harness.py` | The trusted runner against hand-written apps in real Chromium. |
| `test_acceptance_behaviour.py` + `fixtures/adversarial/` | Readable criteria; apps that cheat (hard-coded output, static pages, listed-example lookup). |
| `test_unresolved.py` | Unresolved requirements through propose, approve, build, export/import and restore, for both implementations. |
| `test_framework_workflow.py` | Revision judgment, pause, user approval and continuation through the framework version. |
| `test_deploy.py` | Password gate, `/healthz`, `/preview/<id>/<file>` (traversal, unknown ids), host and port settings, Chromium sandbox fallback. |
| `test_supplied_services.py`, `test_transport.py`, `test_units.py` | Restore/promotion, import hardening, transport against a local fake provider, small units. |
| `test_scenarios.py`, `test_server.py`, `test_ui.py` | End-to-end flows, API, browser UI. |
| `test_live.py` | Real-provider checks; skipped without a key. |

## 8. Reading the "acceptance" ideas correctly
- **Plain-language criteria.** Users approve behaviour in words; selectors stay internal.
- **Varied-example rule.** If an outcome depends on input, a requirement needs at least two checks with different inputs and different expected results. This reduces, but **does not eliminate**, an app that only special-cases the listed examples (fixture `tip_lookup_listed` still passes). These live checks are **not** the protected course assessment, which would need inputs the learner and model never see.
- **Unresolved requirements** can only be replaced with a supported version or removed from scope. There is no "keep it and approve anyway". An app with unresolved items is a partial draft, never "completed".

## 9. Following progress: where to look

**Statuses** (shown at the top of a project):
| Status | Meaning |
|---|---|
| `awaiting_scope_approval` / `awaiting_criteria_approval` / `awaiting_revision_approval` | The app is waiting for you. |
| `needs_clarification` / `unsupported` | The request could not be scoped; read the explanation. |
| `unresolved_requirements` | Something cannot be checked and needs your decision. Nothing can be approved or built yet. |
| `building` | The agent loop is running. |
| `verified` | The last trusted report passed every approved criterion for the current files and criteria. It does not mean the app is correct beyond those criteria. |
| `failed_checks` | The last trusted report failed; the failing criteria are listed. |
| `incomplete` | A build or revision stopped at a cap or error. |
| `unverified` | Files or criteria changed since the last trusted report. |
| `imported_untrusted` | Imported; re-approve and re-check first. |

**Live view:** the event feed in the UI streams each step (model turn, tool call, check result).

**After the fact:** each project lives under the data folder (`./data` by default):
```
data/<project>/
  meta.json        status, phase, unresolved items, last build summary
  app/             the generated files
  approved/        approved scope + criteria (+ resolutions.json), with hashes
  runs/            trace (one JSON line per event), last_acceptance.json, pending.json
  restore/         the single last-working copy
  imported/        proposal from an imported archive
```
- `runs/last_acceptance.json` is the report that decides the status.
- `runs/pending.json` with no build running means the workflow is **paused for a user decision**.
- Trace event types include model calls, tool calls, check results and stop reasons. With the framework version the trace also carries `graph_node` events and `build_start` shows `impl="langgraph"`.
- The same trace is downloadable from `/api/projects/<id>/trace`.

## 10. Troubleshooting
| Symptom | Likely cause / action |
|---|---|
| `playwright` or Chromium errors | Run `playwright install chromium` inside the activated environment. |
| `There is no host setting…` | You tried `APPBUILDER_HOST` or `--host`. Remove it; this is intentional. |
| Port in use | Change `APPBUILDER_API_PORT` / `APPBUILDER_PREVIEW_PORT` in `.env`. |
| Syntax or import errors on Python 3.10 | Expected possibility, since the app has not run on 3.10. Note the exact error and report it; use Python 3.13 meanwhile. |
| Cannot approve criteria | Unresolved items exist. Replace or remove each one. |
| `unverified` after an edit | Working as intended: re-run the trusted check. |
| Live provider errors | Run `tests/test_live.py` first; errors are classified (auth, rate_limit, timeout, network, server, bad_request, bad_response). |

## 11. What not to claim
- No live model has been run; demo mode is not a model.
- It is not safe to host: generated code has no OS-level isolation, the owner token is only an identifier, and learner-held keys do not authorise a hosted app to collect them. See `DEPLOYMENT_BLOCKERS.md`.
- Six-session feasibility is unvalidated and options A to D are unselected.
- Test results come from the authoring container; the device copy is integrity-checked only.

Related: `README.md` (short version), `COMPONENT_MAP.md` (supplied vs learner-owned), `DECISIONS.md` (why), `VALIDATION.md` (evidence), `DEPLOYMENT_BLOCKERS.md` (what a public link still needs).
