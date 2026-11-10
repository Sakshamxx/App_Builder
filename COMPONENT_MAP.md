# Supplied versus learner-owned components

Principle: learners implement the AI orchestration and integrations; supporting infrastructure is supplied behind clear interfaces so
learning mechanisms are never hidden inside a finished abstraction, and failure-prone infrastructure is not a learner's burden.

## Learner-owned (the learning surface)

| Component | File | What the learner decides / implements |
|---|---|---|
| Agent loop, stop rules, caps | `agent/loop.py` | message flow; the step cap; repair counting; stop reasons; "model's word is not evidence" |
| Tool schemas and dispatch | `agent/tools.py` | JSON schemas the model sees; argument parsing; errors returned as data; trusted vs diagnostic checks |
| Prompts | `agent/prompts.py` | system prompts for scope, criteria, build, revision judgment |
| Output contracts | `agent/contracts.py`, `agent/structured.py` | Pydantic models; parse-validate-retry with feedback |
| Scope assessment | `agent/scope.py` | free-form request -> supported scope / unsupported with alternative |
| Criteria translation | `agent/criteria.py` | requirements -> structured criteria; coverage; varied-example rule; unverifiable requirements reported as unresolved |
| Revision judgment | `agent/revision.py` | compatible / conflict / unsupported; merging added criteria |
| Retry policy | `agent/retry.py` | transient vs non-retryable; backoff; separate from repairs |
| Usage, cost, tracing, streaming | `agent/usage.py`, `agent/trace.py` | what is recorded; "unreported" never estimated; redaction before write |
| Status logic | `agent/status.py` | status derived from trusted evidence only |
| Workflow and approval gates | `agent/service.py` | order of steps, hash-bound approvals, resolution of unresolved requirements (replace/remove only), import handling, restore decisions |
| Framework mapping (provisional S5) | `framework/graph.py` | the build/repair loop only: state, nodes, conditional edges, stream modes, backstop vs course caps. Scope, criteria, revision judgment and all user approvals are shared service logic (`agent/service.py`), identical in both implementations |

## Supplied (clear interfaces, not part of the lesson)

| Component | File | Interface the learner uses |
|---|---|---|
| Workspace (confined files) | `supplied/workspace.py` | `list_files/read_file/write_file/delete_file`, `WorkspaceError(code)` |
| Project store, approved contract | `supplied/store.py` | `approved(pid)`, `approve_contract(...)` (called only after a user approval) |
| Criteria vocabulary + validator | `supplied/criteria_vocab.py` | `VOCABULARY_HELP`, `validate_criteria`, `render_readable(criteria, scope)` (user text, no selectors), `render_technical` (builder) |
| Trusted harness + worker | `supplied/harness.py`, `harness_worker.py` | `run_checks(app_dir, criteria, limits, ...)`, `compact_report` |
| Preview file rules and CSP (served at `/preview/<id>/` by `server.py`) | `supplied/preview.py` | `static_file`, `csp`; learners never touch it |
| Restore + promotion safeguard | `supplied/restore.py` | `promote(...)`, `restore(...)` |
| Regression planning | `supplied/regression.py` | `plan(criteria, touched_ids)` (which requirements a change touches is the learner's judgment) |
| Export / hardened import | `supplied/portability.py` | `export_project`, `import_project`, `ImportRejected` |
| Model transport (thin) | `supplied/model_transport.py` | `chat(messages, tools, on_text, ...)` -> `ModelReply` or `TransportError(kind)` |
| Redaction | `supplied/redact.py` | `Redactor` used by the tracer |
| Server and UI | `server.py`, `supplied/ui/*` | HTTP API, SSE, owner token (an identifier, not authentication); optional `APP_PASSWORD` Basic-auth gate, `/healthz`, `/preview/<id>/<file>`; binds `0.0.0.0` on `$PORT` |

## Not delivered, deliberately

Hosting, identity/project authorisation, credential custody, quotas, OS-level execution isolation (all in `DEPLOYMENT_BLOCKERS.md`), a hosted CI route, and a live-model evidence set. The hosting interface is the server module plus `Settings`; a hosting choice can wrap it without touching either column.
