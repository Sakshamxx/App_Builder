# App Builder Agent: Simple Reference Guide

This guide explains the `p1-app-builder-agent` project in plain language. It follows six steps: structure, the agent, LangGraph, testing, running it, and changing it.

---

## 0. The big idea in 5 lines

1. You type what app you want, for example "a tip calculator".
2. The program writes a **scope** (what it will build) and you approve it.
3. The program writes **acceptance criteria** (tests in plain words) and you approve them.
4. An **AI agent** writes the HTML/CSS/JS files, using tools.
5. A **real browser** (Playwright + Chromium) runs the approved tests. Only if all pass is the app called **verified**.

The most important rule of the whole project:

> **The AI's words are never trusted. Only the test report is trusted.**

If the AI says "done, it works", the program still runs the tests itself.

---

## 1. Project structure

### 1.1 Folder map

```
p1-app-builder-agent/
├── appbuilder/              the actual program
│   ├── config.py            all settings and limits in one place
│   ├── server.py            the web server (FastAPI) + API routes
│   ├── agent/               THE AGENT (the part you are meant to learn)
│   ├── framework/graph.py   the same agent loop, written with LangGraph
│   ├── supplied/            helper code given to you (browser tests, storage, UI...)
│   └── testing/             fake "models" for demo mode and for tests
├── tests/                   automated tests (pytest)
├── scripts/run_validation.py  runs all tests and rewrites VALIDATION.md
├── evidence/                screenshots and old test result files
├── requirements.txt         Python libraries needed
├── .env.example             sample settings file
└── *.md                     documents (README, TEAM_GUIDE, DECISIONS, ...)
```

### 1.2 Two kinds of code

The project splits code into two groups. Every file says which group it is in on its first line.

| Group | Folder | Meaning |
|---|---|---|
| **LEARNER-OWNED** | `agent/`, `framework/` | The AI logic. This is what you study and change. |
| **SUPPLIED** | `supplied/`, `server.py` | Plumbing (files, browser, storage, web UI). You use it, you do not need to rewrite it. |

### 1.3 What each agent file does

| File | Job in one sentence |
|---|---|
| `agent/loop.py` | The main agent loop: ask model, run tools, repeat, stop. |
| `agent/tools.py` | The list of tools the model can use, and the code that runs them. |
| `agent/prompts.py` | All the instructions (prompts) given to the model. |
| `agent/contracts.py` | The exact JSON shapes the model must return (Pydantic classes). |
| `agent/structured.py` | Asks the model for JSON, checks it, and asks again if it is wrong. |
| `agent/scope.py` | Step 1: turn the user's request into a scope. |
| `agent/criteria.py` | Step 2: turn the scope into testable criteria. |
| `agent/revision.py` | Decides if a change request fits the approved app. |
| `agent/retry.py` | Tries again when the model provider has a temporary problem. |
| `agent/status.py` | Works out the project status from evidence. |
| `agent/trace.py` | Writes a log of everything that happened. |
| `agent/usage.py` | Counts tokens and cost. |
| `agent/context.py` | A small bag (`RunContext`) holding everything one run needs. |
| `agent/service.py` | The manager: connects all steps and the approval gates. |

### 1.4 What each supplied file does

| File | Job in one sentence |
|---|---|
| `supplied/workspace.py` | A locked box for the generated app's files. |
| `supplied/store.py` | Saves projects on disk, one folder per project. |
| `supplied/criteria_vocab.py` | The small "language" that tests are written in. |
| `supplied/harness.py` | Starts the test runner and collects the report. |
| `supplied/harness_worker.py` | The test runner itself; drives the browser with Playwright. |
| `supplied/preview.py` | A mini web server that shows the generated app. |
| `supplied/restore.py` | Saves the last working version and can bring it back. |
| `supplied/regression.py` | After a change, labels tests as "relevant" or "regression". |
| `supplied/portability.py` | Export a project as a zip, and import one safely. |
| `supplied/model_transport.py` | The only code that talks to the AI provider over HTTP. |
| `supplied/redact.py` | Hides secret keys before anything is logged. |
| `supplied/ui/` | The web page you see at `localhost:8080`. |

### 1.5 Dependencies (from `requirements.txt`)

| Library | Why it is used |
|---|---|
| `fastapi` | Builds the web API. |
| `uvicorn` | Runs the FastAPI server. |
| `httpx` | Sends HTTP requests to the AI provider. |
| `pydantic` | Checks that the model's JSON has the right shape. |
| `playwright` | Controls a real Chromium browser for the tests. |
| `langgraph` | Used only by `framework/graph.py`. |
| `pytest` | Runs the project's own tests. |

### 1.6 Architecture picture

```
 Browser UI (localhost:8080)
        │  HTTP
        ▼
   server.py  (FastAPI routes)
        │
        ▼
 agent/service.py  (workflow + approval gates)
        │
        ├── scope.py / criteria.py / revision.py ──► structured.py ──► model
        │
        └── build loop:  agent/loop.py   OR   framework/graph.py
                              │
                              ├──► model_transport.py ──► Gemini / Ollama / demo stub
                              └──► tools.py ──► workspace.py   (write files)
                                           └──► harness.py ──► harness_worker.py ──► Chromium
```

### 1.7 Where data is saved

Each project gets its own folder inside `data/projects/<id>/`:

| Folder | What is inside |
|---|---|
| `app/` | The generated app. **The only place the agent can write.** |
| `approved/` | The approved scope and criteria. The agent cannot touch this. |
| `restore/` | The last version that passed all checks. |
| `runs/` | Logs: `trace.jsonl`, `usage.jsonl`, `last_acceptance.json`, `pending.json`. |
| `imported/` | Untrusted data from an imported zip. |

---

## 2. The agent

### 2.1 What "agent" means here

An agent is a model inside a loop. The model cannot touch files or run tests itself. It can only **ask** for a tool. Our code runs the tool and gives the result back. This repeats until a stop rule fires.

### 2.2 The whole journey of one request

```
request ──► SCOPE (model) ──► you approve
        ──► CRITERIA (model) ──► you approve
        ──► BUILD LOOP (model + tools) ──► trusted checks ──► status
```

All of this is managed by `AppBuilderService` in `agent/service.py`.

### 2.3 Prompts (`agent/prompts.py`)

Prompts are plain text strings. There are five:

| Prompt | Tells the model to... |
|---|---|
| `SCOPE_SYSTEM` | Decide if the request can be a browser-only app with 1–3 pages. List pages and 3–8 requirements. Say what is unsupported (logins, payments, backend...). |
| `CRITERIA_SYSTEM` | Turn requirements into test steps using only the allowed vocabulary. Use at least two different examples for anything that depends on user input. |
| `BUILD_SYSTEM` | Act as a front-end engineer. Write plain HTML/CSS/JS, add the right `data-testid` attributes, then call `run_checks`. |
| `REVISION_SYSTEM` | Make the smallest change; keep all existing checks passing. |
| `REVISION_JUDGE_SYSTEM` | Label a change request as `compatible`, `conflict` or `unsupported`. |

### 2.4 Structured output (`contracts.py` + `structured.py`)

For scope, criteria and revision judgment, the model must reply with **JSON in an exact shape**.

How `ask_structured` works:

1. Put the JSON schema into the prompt.
2. Get the model's reply and pull the JSON out of it.
3. Check it with Pydantic (and extra rules, if any).
4. If wrong, send the error list back to the model and ask again.
5. After 2 re-asks (`structured_retry_attempts`), give up with `StructuredOutputError`.

The three shapes are `ScopeAssessment`, `CriteriaProposal` and `RevisionJudgment`.

### 2.5 Tools (`agent/tools.py`)

The model sees six tools (`TOOL_SCHEMAS`):

| Tool | What it does |
|---|---|
| `list_files` | Lists the app's files. |
| `read_file` | Reads one file. |
| `write_file` | Creates or replaces one file (full contents). |
| `delete_file` | Deletes one file. |
| `run_checks` | Runs **all approved tests** in a real browser. The only thing that counts as success. |
| `run_diagnostic` | Runs test steps the model invents, to investigate. Never counts as success. |

### 2.6 Tool calling, step by step

1. We send the model the messages **and** the tool schemas.
2. The model replies with tool calls, for example `write_file({"path": "index.html", "content": "..."})`.
3. `dispatch()` checks the tool name, parses the JSON arguments, and runs the right function.
4. The result is added to the conversation as a `tool` message.
5. If something goes wrong, the tool returns `{"ok": false, "error": "..."}`. **It does not crash.** The model reads the error and can fix itself.

Safety rules on tools:

- File names must be flat (no folders), and only `.html`, `.css`, `.js`.
- Maximum 8 files, 64 KB each, 256 KB total.
- Maximum 6 tool calls per model turn. Extra ones are refused.

### 2.7 Memory and state

The agent has no magic memory. It has three simple things:

| What | Where | What it holds |
|---|---|---|
| **Messages** | a Python list in `loop.py` | The whole conversation: system prompt, user task, model replies, tool results. This is the model's "memory" for one build. |
| **BuildState** | a dataclass in `tools.py` | Counters: `repairs`, `writes`, `diagnostics_used`, `awaiting_repair`, and `last_report`. |
| **Disk** | `data/projects/<id>/` | Everything that must survive after the build: files, approved contract, logs, restore point. |

Messages are thrown away after a build. A revision starts a fresh conversation and reads the files again.

### 2.8 The execution loop (`agent/loop.py`)

This is the heart of the project. In simple pseudo-code:

```
messages = [system prompt, user task]
turns = 0
loop:
    if turns >= 20           -> stop "step_cap"
    turns += 1
    reply = ask the model (with retry on network problems)
    add reply to messages
    if reply has tool calls:
        run each tool, add results to messages
        if trusted report is current and all passed  -> stop "checks_passed"
        if repairs used up (3)                        -> stop "repair_cap"
    else (model stopped talking to tools):
        if no current trusted report -> run the checks ourselves
        if passed                    -> stop "checks_passed"
        else tell the model what failed, or stop "model_stopped"
finish: decide status, save restore point if passed
```

### 2.9 Stop rules

| Stop reason | Meaning |
|---|---|
| `checks_passed` | All approved tests passed for the current files. Success. |
| `step_cap` | 20 model turns were used up. |
| `repair_cap` | 3 repair attempts were used and tests still fail. |
| `model_stopped` | The model gave up while tests were failing. |
| `model_unavailable` | The provider could not be reached, even after retries. |
| `model_error` | The provider rejected the request (bad key, bad request). |

Two words that are easy to mix up:

- **Repair** = the first file write after a failed test report. Max 3.
- **Retry** = trying the same model call again after a network/rate-limit problem. Max 3, with waiting time 1s, 2s, 4s... (capped at 20s). Retries do not use up repairs.

### 2.10 "Is the report current?"

A test report stores a hash (fingerprint) of the files and of the criteria. If either changes later, the report is **stale** and no longer counts. This is checked by `is_current()`. It stops the model from passing tests and then quietly changing the files.

### 2.11 Status (`agent/status.py`)

Status is calculated by code from evidence, never from the model.

| Status | Meaning |
|---|---|
| `awaiting_scope_approval` | Waiting for you to approve the scope. |
| `awaiting_criteria_approval` | Waiting for you to approve the tests. |
| `building` | The agent loop is running. |
| `verified` | Last report passed everything, for the current files and criteria. |
| `failed_checks` | The current report has failures. |
| `incomplete` | The build stopped at a cap or failure. |
| `unverified` | Files or criteria changed since the last report. |
| `unsupported` | The request cannot be built as a browser-only app. |
| `needs_clarification` | A change request conflicts with an approved requirement. |
| `unresolved_requirements` | A requirement cannot be tested; you must replace or remove it. |

"Verified" only means **"passed the listed checks"**. It does not mean the app is perfect.

### 2.12 Approval gates and hashes

When you click approve, the browser sends a **hash** of what you saw. If the proposal changed in the meantime, the hashes do not match and the approval is refused. So you can never approve something you did not read.

### 2.13 Revisions (changing an existing app)

When you ask for a change, the model first judges it:

| Verdict | What happens |
|---|---|
| `compatible` | The agent runs the loop again. If new requirements were added, you approve new tests first. |
| `conflict` | Everything stops and you are asked a question ("monthly or weekly?"). |
| `unsupported` | Declined with a reason. |

After a revision, all tests run again. Tests for the changed parts are labelled **relevant**; all others are labelled **regression** (they must keep passing).

### 2.14 Trace and usage

- `trace.py` writes every event (model call, tool call, check report) to `runs/trace.jsonl` and streams it live to the browser. Secrets are hidden first.
- `usage.py` records tokens per model call. Cost is only shown if you set prices in `.env`; otherwise it says "unpriced".

---

## 3. LangGraph

### 3.1 What LangGraph is

LangGraph is a library for drawing your agent as a **graph**: boxes (nodes) joined by arrows (edges), with a shared **state** passed between them.

This project has the same build loop written twice:

- `agent/loop.py`: plain Python `while` loop.
- `framework/graph.py`: LangGraph version.

Both must behave exactly the same. You choose with `APPBUILDER_IMPL=handwritten` or `APPBUILDER_IMPL=framework`.

Only the **build loop** uses LangGraph. Scope, criteria, approvals and revision judgment are shared code in `service.py`.

### 3.2 State

`GraphState` is a typed dictionary. Every node reads it and returns the parts it wants to change.

| Field | Meaning |
|---|---|
| `messages` | The conversation. Uses `operator.add`, so new messages are **appended**, not replaced. |
| `turns` | How many model turns so far. |
| `calls` | Tool calls the model just asked for. |
| `stop` | The stop reason, or `None` to keep going. |
| `error` | Provider error message, if any. |
| `bs` | The same `BuildState` object used by the handwritten loop. |

The `Annotated[list, operator.add]` part is called a **reducer**. It tells LangGraph how to merge a node's output into the state.

### 3.3 Nodes

A node is just a Python function: `state in -> changes out`.

| Node | What it does |
|---|---|
| `model` | Checks the step cap, calls the model once, stores the reply and tool calls. |
| `tools` | Runs the tool calls, then checks the stop rules (`after_tools`). |
| `no_tools` | Runs when the model made no tool call; runs the trusted checks itself (`when_model_stops`). |

The nodes reuse the helper functions from `loop.py`. That is how both versions stay identical.

### 3.4 Edges and conditional routing

```
START ──► model ──(has tool calls)──► tools ────(continue)──► model
            │                           └──(stop)──► END
            ├──(no tool calls)──► no_tools ──(continue)──► model
            │                           └──(stop)──► END
            └──(stop)──► END
```

- A **normal edge** always goes to the same place: `g.add_edge(START, "model")`.
- A **conditional edge** uses a small function to pick the next node.

The two routing functions:

- `after_model`: if `stop` is set go to `END`; else if there are tool calls go to `tools`; else go to `no_tools`.
- `after_node`: if `stop` is set go to `END`; else go back to `model`.

### 3.5 Running and streaming

`graph.stream(...)` is called with two stream modes:

- `"updates"`: one message each time a node finishes. Logged as a `graph_node` trace event.
- `"custom"`: text pieces the model node writes while the model is typing.

### 3.6 The recursion limit

LangGraph has its own safety limit (`recursion_limit`). Here it is set high (`4 × 20 + 12 = 92`) so the project's own caps (20 turns, 3 repairs) always fire first. If the LangGraph limit is ever hit, the stop reason is `framework_backstop`.

### 3.7 What is NOT used

No checkpointer, no interrupts, no saved graph state. A "pause for approval" is simply the service function returning and waiting for your next click.

### 3.8 Side by side

| Idea | Handwritten (`loop.py`) | LangGraph (`graph.py`) |
|---|---|---|
| Loop | `while stop is None:` | arrows going back to `model` |
| Deciding what is next | `if reply.tool_calls: ... else: ...` | conditional edges |
| Conversation | a local list `messages` | `state["messages"]` with a reducer |
| Counters | local variables | fields in `GraphState` |
| Stop | `break` | route to `END` |

---

## 4. The testing system

There are two different things called "testing" here. Do not mix them up.

| | A. The trusted harness | B. The project's own tests |
|---|---|---|
| What it tests | The **generated app** | The **app builder itself** |
| When it runs | During every build | When a developer runs `pytest` |
| Where | `supplied/harness*.py` | `tests/` |

### 4.1 Acceptance criteria

Criteria are not code. They are small JSON steps from a fixed list. Example:

```json
{"id": "C1", "title": "Tip is calculated", "requirement_ids": ["R1"],
 "steps": [
   {"action": "navigate", "page": "index.html"},
   {"action": "enter", "target": "bill", "value": "80"},
   {"action": "select", "target": "tip-percent", "value": "15"},
   {"action": "click", "target": "calculate"},
   {"action": "assert_text", "target": "tip-amount", "equals": "12.00"}
 ]}
```

`target` is a `data-testid` attribute in the HTML, for example `<span data-testid="tip-amount">`.

The nine allowed steps (`criteria_vocab.py`):

| Step | Meaning |
|---|---|
| `navigate` | Open a page. |
| `enter` | Type into an input. |
| `select` | Choose from a dropdown. |
| `filter` | Set a filter control. |
| `click` | Click a button or link. |
| `reload` | Reload the page (tests that data is saved). |
| `assert_text` | Text equals or contains a value. |
| `assert_count` | Number of matching elements. |
| `assert_visible` | Element is visible. |

Anything else is rejected. Things that cannot be tested this way (colours, animation, sound) are marked **unverifiable**, and you must replace or remove them before building.

The same JSON is shown in two ways:

- `render_readable`: plain words for you ("Type 80 into Bill amount"). No test ids.
- `render_technical`: with test ids, for the model that builds the app.

### 4.2 The "vary the examples" rule

If a result depends on user input, there must be **two tests with different inputs and different answers**. Why? An app that just prints "12.00" always would pass a single test. This is checked by `variation_problems()` in `criteria.py`.

### 4.3 How Playwright runs the checks

1. `harness.run_checks()` validates the criteria.
2. It starts `harness_worker.py` as a **separate process** with a cleaned environment (no API keys).
3. The worker serves the `app/` folder on a random local port.
4. The worker opens headless Chromium with Playwright.
5. For **each criterion** it opens a brand new browser context (empty storage), runs the steps, and prints one JSON result line.
6. The parent collects the lines into one report.

Protections while checking:

- Any request to another website is blocked and recorded.
- WebSockets and service workers are blocked.
- Each step has a 3 second timeout; the whole run has a 90 second timeout.
- The clock can be fixed to a set date so date-based tests are repeatable.

Each criterion ends as `passed`, `failed`, `errored` or `not_run`.

### 4.4 Automated repair

There is no separate "repair program". Repair is simply the loop continuing:

1. `run_checks` returns a failed report with messages like *"expected [total] to read '92.00'; found '92'"*.
2. This goes back to the model as a tool result.
3. The model reads it and calls `write_file` with a fix. This first write counts as **repair 1**.
4. The model calls `run_checks` again.
5. This repeats up to **3 repairs**. Then the loop stops with `repair_cap` and shows a clear failure report.

### 4.5 Restore point

When all checks pass, `promote()` copies the files into `restore/`. Only one restore point is kept. If a later change breaks the app, "Restore last working version" brings it back.

### 4.6 The project's own tests (`tests/`)

| File | Test functions | What it covers |
|---|---|---|
| `test_units.py` | 8 | Small pieces: settings, usage, redaction, retry, JSON extraction. |
| `test_harness.py` | 11 | The browser harness on hand-written apps. |
| `test_acceptance_behaviour.py` | 11 | Readable criteria text, the vary-the-examples rule, cheating apps must fail. |
| `test_boundaries.py` | 14 | Safety limits: files, network, secrets. |
| `test_scenarios.py` | 33 | Full builds, repairs, caps, revisions (run for both loops). |
| `test_framework_workflow.py` | 2 | The LangGraph path. |
| `test_unresolved.py` | 10 | Requirements that cannot be tested. |
| `test_supplied_services.py` | 11 | Store, restore, export/import. |
| `test_server.py` | 5 | The HTTP API. |
| `test_local_only.py` | 8 | The server refuses non-local access. |
| `test_transport.py` | 6 | Talking to a fake provider. |
| `test_ui.py` | 1 | The web page, clicked by Playwright. |
| `test_live.py` | 3 | Real-model checks (skipped without a key). |

Tests use `ScriptedTransport` (`testing/scripted.py`), a fake model that replays prepared replies. This makes tests repeatable and free.

`tests/fixtures/adversarial/` holds deliberately cheating apps (for example one that always prints the same numbers) to prove the checks catch them.

---

## 5. Running the project locally

### 5.1 Setup (macOS / Linux)

Run these inside the `p1-app-builder-agent` folder:

```bash
python3 -m venv .venv                 # make a private Python environment
source .venv/bin/activate             # switch it on
pip install -r requirements.txt       # install the libraries
playwright install chromium           # download the test browser
cp .env.example .env                  # create your settings file (demo mode)
set -a && source .env && set +a       # load the settings
python -m appbuilder.server           # start the server
```

Then open **http://localhost:8080** in your browser.

### 5.2 Try the demo

1. Type `a tip calculator` and submit.
2. Read the scope and approve it.
3. Read the criteria and approve them.
4. Watch the build. The status becomes **verified** and a preview appears.
5. Type `an online shop with payments` to see the **unsupported** path.

Demo mode is **not a real AI**. It is a script (`testing/demo.py`) that knows three apps: tip calculator, habit tracker, expense tracker.

### 5.3 What happened when this was run on this machine

Set up and run on 10 Nov 2026 with Python 3.13.3 and Chromium 153:

| Check | Result |
|---|---|
| Install of all libraries | OK |
| Demo "a tip calculator", handwritten loop | **verified**, 4 of 4 checks passed, 2 model turns, 0 repairs, about 4 seconds |
| Demo "a tip calculator", LangGraph loop | **verified**, 4 of 4 checks passed, same result |
| Demo "an online shop with payments" | **unsupported**, alternative offered |
| Preview at port 8081 | Page served (HTTP 200) |
| Full test suite (`pytest`) | TEST_RESULT_PLACEHOLDER |

Events seen in the trace for one build (handwritten):

```
scope_assessment_start → model_call → scope_assessment_end
criteria_translation_start → model_call → criteria_translation_end
build_start → model_call → tool_call (write_file)
            → model_call → check_report → tool_call (run_checks) → build_end
```

The LangGraph run shows the same events plus `graph_node` lines for each node.

### 5.4 Settings (`.env`)

| Setting | Meaning |
|---|---|
| `APPBUILDER_PROVIDER` | `demo`, `gemini` or `ollama`. |
| `APPBUILDER_MODEL` | The model name. |
| `GEMINI_API_KEY` | Your Gemini key (only for `gemini`). |
| `APPBUILDER_IMPL` | `handwritten` or `framework` (LangGraph). |
| `APPBUILDER_DATA_DIR` | Where projects are saved (`./data`). |
| `APPBUILDER_API_PORT` | UI port (8080). |
| `APPBUILDER_PREVIEW_PORT` | Generated app port (8081). |

### 5.5 Using a real model

1. In `.env` set `APPBUILDER_PROVIDER=gemini`, `APPBUILDER_MODEL=<model id>`, `GEMINI_API_KEY=<key>`. Or use `ollama` with a local model that supports tool calling.
2. Reload the settings and restart the server.
3. First run: `APPBUILDER_LIVE=1 python -m pytest tests/test_live.py -v`.

Note: the project's own documents say **no real model has been tested yet**. Expect to fix small things in `model_transport.py`.

### 5.6 Running tests

```bash
python -m pytest                           # everything (several minutes)
python -m pytest tests/test_units.py -v    # one file
```

### 5.7 Common problems

| Problem | Fix |
|---|---|
| "Address already in use" | Another program uses port 8080/8081. Change the ports in `.env`. |
| Browser checks fail to start | Run `playwright install chromium` again. |
| Everything says "unsupported" in demo | Demo only knows tip / habit / expense apps. |
| Cannot open from another computer | By design. The server only answers `localhost`. |
| Settings ignored | You forgot `set -a && source .env && set +a` in this terminal. |

---

## 6. Modifying and improving

No source code was changed while writing this guide. Below are safe first changes, from easy to harder.

### 6.1 Good habits first

- Change one thing at a time.
- After each change, run the demo and look at `data/projects/<id>/runs/trace.jsonl`.
- Run `python -m pytest tests/test_scenarios.py` to see if you broke the loop.

### 6.2 Starter exercises

| # | Exercise | File to change | What you learn |
|---|---|---|---|
| 1 | Change `max_repairs` from 3 to 1 and watch a build stop earlier. | `config.py` | Caps and stop rules. |
| 2 | Edit a sentence in `BUILD_SYSTEM` and compare traces. | `agent/prompts.py` | How prompts steer the model. |
| 3 | Add a new demo app (for example a counter). | `tests/fixtures_data.py`, `tests/fixtures/apps/`, `testing/demo.py` (`KEYWORDS`) | How scope, criteria and files fit together. |
| 4 | Add a new tool, for example `search_files(text)`. | `agent/tools.py` (schema + branch in `dispatch`) | Tool schemas and dispatch. |
| 5 | Add a new node to the graph, for example a `summarise` node before `END`. | `framework/graph.py` | Nodes and edges. |
| 6 | Add a new test step such as `assert_hidden`. | `supplied/criteria_vocab.py` (`ACTIONS`, `_describe`) and `supplied/harness_worker.py` (`run_step`) | How criteria become browser actions. |
| 7 | Print a one-line summary at the end of each build. | `agent/loop.py` (`finish`) | Outcome and tracing. |

### 6.3 Recipe: adding a new tool

1. Add a `_schema(...)` entry to `TOOL_SCHEMAS` with name, description and parameters.
2. Add an `if name == "your_tool":` branch in `dispatch()` that returns `{"ok": True, ...}`.
3. Return errors as data (`{"ok": False, "error": "..."}`), never raise.
4. Mention the tool in `BUILD_SYSTEM` so the model knows when to use it.
5. Add a test in `tests/test_scenarios.py` using `ScriptedTransport`.

Both loops pick the tool up automatically, because both use the same `tools.py`.

### 6.4 Rules to keep when you change things

- Never let the model decide success. Success comes only from `run_checks`.
- Never let the agent write outside `app/`.
- Keep the caps. A loop without limits can run forever and cost money.
- If you change `loop.py` behaviour, make the same change work in `graph.py`.
- Never put an API key in code or commit `.env`.

### 6.5 Known limits (from the project's own documents)

- Local use only. A public deployment needs real isolation, logins and quotas (see `DEPLOYMENT_BLOCKERS.md`).
- No real model has been validated yet.
- A clever app that hard-codes exactly the listed examples can still pass. Varied examples reduce this but do not remove it.

---

## Mini glossary

| Word | Simple meaning |
|---|---|
| Agent | A model in a loop that can call tools. |
| Tool | A function the model may ask our code to run. |
| Tool schema | The description of a tool that the model sees. |
| Dispatch | Picking and running the right function for a tool call. |
| Scope | What will be built: pages and requirements. |
| Acceptance criteria | The approved tests. |
| Harness | The trusted test runner. |
| Playwright | A library that controls a browser from code. |
| `data-testid` | A label on an HTML element so tests can find it. |
| Hash | A fingerprint of some data; changes if the data changes. |
| Repair | A fix attempt after failed checks. |
| Retry | Repeating a model call after a temporary provider error. |
| Node | One step (function) in a LangGraph graph. |
| Edge | An arrow from one node to the next. |
| Conditional edge | An arrow chosen by a function at run time. |
| State | Data shared between nodes. |
| Reducer | The rule for merging a node's output into the state. |
| Trace | The log of everything that happened in a run. |
| Restore point | The last saved version that passed all checks. |
