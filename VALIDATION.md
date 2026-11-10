# Project 1 validation report

**Prototype—live-model and delivery validation outstanding.**

> **Note (added after this report was generated):** the loopback-only guard described below was removed for a password-protected demo deployment, and `tests/test_local_only.py` was replaced by `tests/test_deploy.py`. The "Local-only guard" row and item 3 describe the earlier behaviour and were not re-run. Re-run `scripts/run_validation.py` to regenerate this report with the current tests.

Generated 2026-11-06 04:50 UTC by `scripts/run_validation.py` from the junit XML files of the actual pytest runs listed under Environment (`evidence/junit*.xml`).

## Result

**Collected 241: 238 passed, 0 failed, 3 skipped** (sum of per-test times 834s; runs were split across processes, so this is not elapsed time).

Counts by evidence category (a pass in one category says nothing about another):

| Category | Collected | Passed | Failed | Skipped |
|---|---|---|---|---|
| http-and-demo-flow | 5 | 5 | 0 | 0 |
| live-model | 3 | 0 | 0 | 3 |
| local-fake-provider | 9 | 9 | 0 | 0 |
| real-browser-check | 80 | 80 | 0 | 0 |
| scripted-agent-scenario | 92 | 92 | 0 | 0 |
| unit-and-http-guard | 24 | 24 | 0 | 0 |
| unit-and-service | 28 | 28 | 0 | 0 |

## Evidence levels used (read this before trusting any row)

- **scripted-agent-scenario**: see *scripted stub* below. **real-browser-check**: see *real browser / HTTP*. **live-model**: see *LIVE*. **http-and-demo-flow**: a real local server driven with the labelled demo transport (not a model). **unit-and-service / unit-and-http-guard**: code paths with no browser and no model.
- **real browser / HTTP**: real Chromium, real local servers, hostile or hand-written pages. Evidence about our infrastructure.
- **scripted stub**: a deterministic stand-in replays chosen model replies (`appbuilder/testing/scripted.py`). Proves OUR code's behaviour when the model behaves that way. **It is not evidence about any real model.**
- **local fake provider**: a local HTTP server imitating an OpenAI-compatible API. Proves our transport handles those shapes. **Real Gemini field shapes are unverified.**
- **LIVE**: a real configured model. None were run (see Outstanding).

## Environment

- Python 3.13.18 on Linux-6.18.44-fc-v242-x86_64-with-glibc2.39
- fastapi 0.162.0, uvicorn 0.54.0, httpx 0.28.1, pydantic 2.13.5, playwright 1.63.0, langgraph 1.2.20, pytest 9.1.1
- Model configuration for ALL recorded runs: provider `stub`/`demo`, model `scripted-model`/`demo-stub` (no real model was called). Token and latency numbers in scripted runs are the script's, not a provider's.
- Limits under test: `max_model_turns=20`, `max_repairs=3`, `max_tool_calls_per_turn=6`, `transient_retry_attempts=3`, `max_diagnostics=5`, `check_timeout_s=90`, `step_timeout_ms=3000` (see `appbuilder/config.py`).
- Chromium ran as root with `--no-sandbox` in this container; the harness records `chromium_sandbox` in every report (see Limitations).
- Commands run (from the project folder): `python -m pytest tests/test_scenarios.py -q -p no:cacheprovider -k handwritten --junitxml=evidence/junit-a_hw.xml`; `python -m pytest tests/test_scenarios.py -q -p no:cacheprovider -k framework --junitxml=evidence/junit-b_fw.xml`; `python -m pytest tests/test_harness.py tests/test_boundaries.py tests/test_acceptance_behaviour.py -p no:cacheprovider --junitxml=evidence/junit-c.xml`; `python -m pytest tests/test_ui.py tests/test_server.py tests/test_unresolved.py tests/test_framework_workflow.py tests/test_local_only.py tests/test_transport.py tests/test_supplied_services.py tests/test_units.py tests/test_live.py -p no:cacheprovider --junitxml=evidence/junit-d.xml`
- Run location: the authoring container, from the build copy. **Not run from the copy in the course folder on the learner's device (Python 3.10 there).** That copy was checksum-verified only.

## Requirement-to-evidence map

| Behaviour | Evidence level | Tests | Result |
|---|---|---|---|
| One-, two- and three-page apps built and verified (both implementations) | scripted stub | 1 test functions | 6 passed |
| Navigation, interactions, calculations, persistence checked by the trusted harness | real browser, hand-written apps | 2 test functions | 4 passed |
| Failed check, then bounded repair | scripted stub | 2 test functions | 3 passed |
| Cap reached -> incomplete status + explicit failure report (repair cap, step cap) | scripted stub | 2 test functions | 4 passed |
| Model's claim of success is not evidence; loop runs the trusted check | scripted stub | 2 test functions | 4 passed |
| Agent diagnostics are labelled and never count as acceptance | scripted stub | 3 test functions | 5 passed |
| Agent cannot reach or edit approved criteria | scripted stub + unit | 4 test functions | 18 passed |
| Unsupported requests (whole and partial) and unverifiable criteria | scripted stub | 6 test functions | 16 passed |
| Approvals bound to what the user saw | scripted stub | 1 test functions | 2 passed |
| Compatible revision with regression checking | scripted stub | 4 test functions | 7 passed |
| Conflicting revision -> clarification, no change | scripted stub | 3 test functions | 6 passed |
| Restore after a failed revision; one restore point; promotion safeguard | scripted stub + unit | 7 test functions | 9 passed |
| Same-browser save/reopen; export; import in a fresh browser profile | real browser UI (demo mode) | 1 test functions | 1 passed |
| Export/import hardening; imported projects untrusted, re-approved, re-checked | unit + scripted stub | 7 test functions | 16 passed |
| Streaming, tracing, recorded usage and cost | scripted stub + local fake provider | 5 test functions | 6 passed |
| Transient failures retried with backoff, separate from repairs; auth not retried | scripted stub + unit | 6 test functions | 12 passed |
| Execution boundary: network blocked by harness alone, by CSP alone (with an uncontrolled control run) | real browser, hostile page | 3 test functions | 3 passed |
| Execution boundary: secrets/env, storage origin, path traversal, methods | real browser + HTTP | 9 test functions | 22 passed |
| Owner scoping and concurrency | scripted stub + HTTP | 3 test functions | 5 passed |
| Runaway page is stopped by the harness timeout | real browser | 1 test functions | 1 passed |
| Unresolved requirements block approval, build and promotion; replace/remove only; kept through proposal, storage, restore, export/import | scripted stub + unit | 10 test functions | 20 passed |
| Readable criteria carry no selectors and come from the same structured criteria the harness runs | unit | 4 test functions | 6 passed |
| Varied-example rule (proposal time) | unit | 4 test functions | 8 passed |
| Hardcoded outputs pass one example but fail varied criteria; wrong behaviour with right test ids fails | real browser, hand-written adversarial apps | 3 test functions | 7 passed |
| RESIDUAL GAP: an app special-casing exactly the listed examples passes them (held-out input exposes it) | real browser, hand-written adversarial app | 1 test functions | 1 passed |
| Framework implementation: revision judgment, pause, approval, continuation, repair (service workflow; NOT a graph interrupt/checkpoint test) | scripted stub | 2 test functions | 2 passed |
| Local-only guard: loopback bind, loopback clients and Host names only, no override (NOT OS containment) | unit + ASGI | 8 test functions | 24 passed |
| LIVE model: tool-call shape, streaming deltas, usage fields | LIVE (skipped unless configured) | 3 test functions | skipped (not run) |

Every scenario marked *scripted stub* runs against **both** implementations (`handwritten` loop and `framework` LangGraph), parametrised in `tests/test_scenarios.py`.

## Tests not listed in the map above (infrastructure and unit coverage)

`test_a_diagnostic_report_is_never_current_even_if_its_hashes_match`, `test_compact_report_for_model_hides_nothing_needed`, `test_contracts_reject_bad_scope_and_conflict_without_question`, `test_csp_forbids_framing_by_other_sites_and_connect_to_others`, `test_extra_tool_calls_in_one_turn_are_refused_as_data`, `test_extract_json`, `test_missing_element_is_failed_not_errored`, `test_non_streaming_reply_tool_calls_and_usage`, `test_page_that_does_not_exist_fails`, `test_redactor_masks_configured_and_common_key_shapes`, `test_resolve_item_action_exists_and_refuses_when_nothing_is_unresolved`, `test_settings_from_env_defaults_and_providers`, `test_structured_output_retry_then_clear_failure`, `test_the_key_never_appears_in_repr_or_error_text`, `test_tracer_redacts_before_file_and_sink`, `test_unsupported_request_in_demo_mode_and_unknown_action`

## Outstanding (not demonstrated)

1. **No live model was run.** No key was authorised. `tests/test_live.py` (3 tests) is ready and skips until `APPBUILDER_LIVE=1` plus provider, model and key are set. Unverified until then: Gemini's tool-call JSON shape and streamed tool-call deltas on the OpenAI-compatible endpoint, the exact usage field names, whether `response_format` JSON modes are honoured, and how well a real model follows the build, criteria and revision prompts.
2. **Real model quality, cost and latency** are therefore unmeasured. Cost shows `unpriced` unless `APPBUILDER_PRICE_INPUT_PER_M` / `_OUTPUT_PER_M` are set.
3. **No public hosting route** is confirmed; this report covers local operation only, and the application refuses to be exposed beyond loopback. Identity/project authorisation, credential custody, quotas and OS-level execution isolation are separate unmet requirements (`DEPLOYMENT_BLOCKERS.md`).
3a. **This run was in the authoring container.** The copy of the project placed in the course folder on the learner's device was integrity-checked by checksum, which shows the files transferred intact and nothing else. The application and its tests have **not been run from that copy** or in the device's Python (3.10) environment.
4. **Six-session feasibility** is unvalidated. Nothing here measures it and none is inferred from code size.
5. **Test gaming is reduced, not eliminated.** Varied examples stop a page that reproduces one example; a page that special-cases every listed example passes (see the RESIDUAL GAP row). The live acceptance checks are not a protected course assessment.
6. **Same-user workers, browser request blocking and timeouts are not OS containment.** The boundary tests show behaviour for the cases they exercise only.

## Limitations of the execution boundary (verified facts, not assurances)

- Chromium's OS sandbox is disabled when running as root. Production hosting must add container isolation; the report field `conditions.chromium_sandbox` records which mode ran.
- Playwright's sync API deadlocked when closing a WebSocket inside its handler (found by the boundary tests). WebSockets are now mocked and never connected; the test proves no handshake reaches an outside listener.
- A CSP-blocked form post leaves the page's `load` event pending in a plain browser; the harness avoids this because blocked requests are aborted.
- `serviceWorker.register()` may resolve under Playwright's `service_workers="block"`; the test asserts no registration exists.
- Isolation covers network egress from the page, process environment, storage origin and file access. It does not claim to stop CPU/memory exhaustion beyond the harness timeout.
- The owner token separates one browser's projects from another's; it is an identifier, not authentication or project authorisation, and is not a boundary between people sharing a browser profile. A shared access code would not change that.
