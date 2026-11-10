# Decisions, rationale and open approvals (Project 1 reference app)

**Prototype—live-model and delivery validation outstanding.**

Chat-only decisions from the planning thread are not recorded anywhere else; this file records the implementation choices made while building. Nothing here changes curriculum approval state, the syllabus, standards or templates.

## A. Reversible implementation choices (made; change freely)

| Choice | Rationale | How to reverse |
|---|---|---|
| Plain-Python loop is the reference; LangGraph version reproduces it | "Plain loop first, then framework against the same requirements" | `APPBUILDER_IMPL` switches at runtime; both pass the same scenario suite |
| Thin HTTP transport on `httpx`, not an LLM SDK | Exposes raw messages, tool calls and usage, so nothing about the message flow is hidden; one provider-neutral surface for Gemini's OpenAI-compatible endpoint and Ollama | Replace `supplied/model_transport.py`; the agent only needs `chat(messages, tools, on_text)` |
| Structured output mode `prompt_only` by default | Gemini's compat page does not document `json_schema` bodies; validating + retrying is needed anyway | `APPBUILDER_STRUCTURED_MODE` |
| Model id is explicit config, never defaulted | Model ids change; guessing one would fabricate a configuration | n/a |
| Bounded criteria vocabulary (navigate, enter, select, filter, click, reload, assert_text/count/visible) addressed by `data-testid` | The agent never writes test code; anything outside is rejected explicitly or listed "unverifiable" | Extend `criteria_vocab.py` + `harness_worker.py` together |
| Test ids are an internal convention. Users approve `render_readable(criteria, scope)` (plain labels, under the requirement text); the builder gets `render_technical` | Both renderings are generated from the same structured criteria the harness runs, so the approved meaning and the executed checks cannot drift apart. Adding a matching element alone does not pass: the harness checks observable results | `criteria_vocab.py` |
| Where an outcome depends on user input, a proposal needs two criteria for the same requirement with different inputs and different expected results | A page that reproduces one listed example would otherwise pass. It adds no requirement; the second example only restates the same behaviour | `agent/criteria.py: variation_problems` |
| Approvals bound to a hash of what the user saw | Prevents approving one thing and committing another | Drop the hash argument in `service.approve_*` |
| Criteria exist BEFORE the build, so generated apps must expose the criteria's testids | The user approves the definition of "verified" first; the agent cannot move the goalposts | n/a (core to the design) |
| Status is derived from the last trusted report for the current files + criteria hashes | The model's claim of success is never evidence | `agent/status.py` |
| Repair = first write after a failed trusted report; transient retries counted separately | Keeps "generated code failing checks" apart from "provider hiccups" | `Limits` |
| `fixed_time` via Playwright's clock; clean storage per criterion; fresh context per criterion | Deterministic date logic and no state leaking between checks | harness worker |
| One owner token per browser in localStorage | Same-browser saving without accounts. It is an identifier, **not authentication and not project authorisation** | Replace `owner()` dependency in `server.py` (see `DEPLOYMENT_BLOCKERS.md`; no option selected) |
| Demo transport (`APPBUILDER_PROVIDER=demo`) | Lets the app be exercised without any key; permanently labelled "not a model" | delete `testing/demo.py` and the branch in `service._default_transport` |
| Dependencies (verified from installed package metadata): FastAPI MIT, uvicorn BSD-3, httpx BSD-3, Pydantic MIT, Playwright Apache-2.0, LangGraph MIT, pytest MIT | Free, permissive. Earlier in the thread I verified Pydantic/pytest/Playwright/LangGraph licences from their official pages; FastAPI/uvicorn/httpx were checked from package metadata only, not their official docs | n/a |

## B. Boundary findings that changed the design (from the boundary tests)

1. Closing a WebSocket inside Playwright's sync handler deadlocks the worker (hung until the 90 s harness timeout; the timeout contained it). Fixed: sockets are mocked and never connected; a test proves no handshake reaches an outside listener.
2. Each layer is tested alone: harness blocking with the CSP switched off, and the CSP in a plain browser with no harness routing, plus a control run showing the hostile page does exfiltrate when both are off. A preview iframe or ordinary subprocess is not treated as isolation.
3. `serviceWorker.register()` resolves under Playwright's blocking mode, so the test asserts no registration exists rather than that the call fails.

## C. Items for your approval (material, or touching learning scope)

1. **Framework version scope.** LangGraph replaces the *build loop* (nodes, edges, state, streaming). The single-shot structured calls (scope, criteria, revision judgment) are shared code in both versions. If the course wants learners to express those as graph nodes too, that is more framework work in S4-S5; I have not decided that for you.
2. **Message representation in the graph.** State carries plain OpenAI-style dicts and our own `dispatch`, rather than LangChain message/tool abstractions, so tool schemas, dispatch and message flow stay visible. Using LangChain tool/message objects would be more idiomatic LangGraph but hides those mechanisms.
3. **The data-testid contract** (criteria first, app conforms) is a learner-facing design constraint. Alternatives (agent proposes testids after building) weaken the approval-before-build property.
4. **Unverifiable requirements (changed in the correction pass).** They no longer enter an approved contract. They stay visible as *unresolved* in the proposal, in storage (`meta.unresolved`), in export/import, and across restore. Approval, build and promotion are refused until the user, for each one, approves a supported replacement or explicitly removes it from scope (recorded in `approved/resolutions.json`). There is no "keep unresolved and approve" option. A partial draft can exist but is shown as incomplete, never `verified`.
5. **No paid selection, billing, hosting or purchase** was made. Hosting stays behind `server.create_app` + `Settings`.

## D. Framework mapping, shared logic and teaching placement

- **Teaching placement:** the framework mapping belongs to provisional **S5**. (An earlier statement placing it in S6 was wrong.)
- **What is graph-based:** only the build/repair loop (`framework/graph.py`: nodes `model`, `tools`, `no_tools`, conditional edges, `updates`+`custom` streaming; `recursion_limit` is a backstop, the course caps are enforced in our nodes).
- **What is shared service logic in both implementations:** scope assessment, criteria translation, revision judgment, resolution of unresolved requirements, every user-approval gate and its hash, pending state (`runs/pending.json`), restore, export/import. A "pause for approval" is the service returning with a pending record and no build running; the user's decision is a later service call that then starts the chosen build loop.
- **Evidence:** `tests/test_framework_workflow.py` runs revision judgment -> pause -> user approval -> continuation -> failed check -> repair -> verified through the framework implementation. It is a service-workflow test. It is **not** a LangGraph interrupt or checkpoint test; the graph has no checkpointer and no interrupts, and none was added.
- **Feasibility:** nothing in this repository establishes six-session feasibility, and none is inferred from code size, file counts or structure. That needs trials with learners. Options A-D from the planning thread remain unselected.

## E. Live acceptance checks versus protected assessment, and test gaming

- The live acceptance checks are the criteria the user approved. They are visible to the model that builds the app.
- Requiring varied examples closes the case where one example is reproduced (`tests/fixtures/adversarial/*`). It does not stop an app that special-cases every listed example: `tip_lookup_listed` passes the listed criteria and fails an input the criteria never showed (`test_listed_example_lookup_passes_the_listed_criteria_and_unseen_inputs_expose_it`). Hidden variants would help only if they stay hidden from the builder, and even then they would not eliminate gaming.
- A **protected course assessment** is a separate thing: held-out inputs and checks that the learner's agent never sees. It is not implemented here and the live checks must not be described as one.
- Expected values in extra examples are worked out by the model from the requirement and reviewed by the user; a consistently wrong expectation approved by the user is not caught.
