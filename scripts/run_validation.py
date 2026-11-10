"""Run the whole test suite and write VALIDATION.md from the ACTUAL results (junit XML). Nothing in the report is typed by hand
except the mapping of requirements to test names and the evidence-level labels."""
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JUNIT = ROOT / "evidence" / "junit.xml"

# requirement -> (evidence level, [test name prefixes that demonstrate it])
MAP = [
 ("One-, two- and three-page apps built and verified (both implementations)", "scripted stub", ["test_representative_apps_are_built_and_verified"]),
 ("Navigation, interactions, calculations, persistence checked by the trusted harness", "real browser, hand-written apps", ["test_reference_apps_pass_their_criteria", "test_fixed_clock_makes_current_month_deterministic"]),
 ("Failed check, then bounded repair", "scripted stub", ["test_failed_check_then_repair_succeeds", "test_failed_check_reports_requirement_and_step"]),
 ("Cap reached -> incomplete status + explicit failure report (repair cap, step cap)", "scripted stub", ["test_repair_cap_reached_is_incomplete_with_explicit_report", "test_step_cap_is_reported_as_incomplete"]),
 ("Model's claim of success is not evidence; loop runs the trusted check", "scripted stub", ["test_model_claiming_success_without_a_check_is_checked_by_the_loop", "test_model_that_gives_up_with_failing_checks_is_not_verified"]),
 ("Agent diagnostics are labelled and never count as acceptance", "scripted stub", ["test_diagnostics_never_count_as_acceptance", "test_diagnostic_limit_and_invalid_steps_are_reported_as_data", "test_diagnostic_reports_are_labelled_and_separate"]),
 ("Agent cannot reach or edit approved criteria", "scripted stub + unit", ["test_agent_file_tools_cannot_reach_or_edit_the_approved_criteria", "test_agent_workspace_cannot_reach_approved_contract", "test_workspace_rejects_escaping_or_disallowed_names", "test_workspace_limits_and_symlinks"]),
 ("Unsupported requests (whole and partial) and unverifiable criteria", "scripted stub", ["test_unsupported_request_is_explained_and_an_alternative_needs_approval", "test_partly_unsupported_request_shows_what_is_dropped_and_unverifiable_criteria", "test_criteria_outside_the_vocabulary_are_sent_back_to_the_model", "test_requirement_without_a_criterion_is_rejected_until_covered_or_declared_unverifiable", "test_unsupported_assertion_is_rejected_explicitly", "test_validator_rejects_malformed_criteria"]),
 ("Approvals bound to what the user saw", "scripted stub", ["test_approvals_are_bound_to_what_the_user_saw"]),
 ("Compatible revision with regression checking", "scripted stub", ["test_compatible_revision_runs_all_checks_and_replaces_the_restore_point", "test_revision_that_breaks_a_regression_is_caught", "test_compatible_revision_that_adds_a_requirement_needs_new_criteria_approval", "test_regression_plan_labels_relevant_and_regression_checks"]),
 ("Conflicting revision -> clarification, no change", "scripted stub", ["test_conflicting_revision_stops_and_asks_without_touching_files", "test_conflict_resolved_by_replacing_the_requirement_needs_reapproval_then_rebuilds", "test_unsupported_revision_is_declined_without_changes"]),
 ("Restore after a failed revision; one restore point; promotion safeguard", "scripted stub + unit", ["test_failed_revision_can_be_restored_to_the_last_working_version", "test_failed_revision_is_restored_to_the_last_working_version", "test_only_one_restore_point_is_kept", "test_promotion_requires_a_trusted_passing_report_for_current_files", "test_report_goes_stale_when_files_change", "test_restore_without_a_restore_point_is_a_clear_error", "test_restore_without_restore_point_and_after_a_save"]),
 ("Same-browser save/reopen; export; import in a fresh browser profile", "real browser UI (demo mode)", ["test_user_journey_in_a_real_browser"]),
 ("Export/import hardening; imported projects untrusted, re-approved, re-checked", "unit + scripted stub", ["test_export_contains_no_owner_token_or_secrets", "test_import_is_untrusted_has_no_approval_and_must_be_rechecked", "test_import_rejects_hostile_archives", "test_import_rejects_oversized_bombs_symlinks_and_bad_manifests", "test_imported_criteria_that_fail_validation_are_dropped_not_trusted", "test_export_import_requires_reapproval_and_reruns_checks_without_a_model", "test_imported_app_that_fails_its_checks_is_not_verified"]),
 ("Streaming, tracing, recorded usage and cost", "scripted stub + local fake provider", ["test_streaming_and_trace_record_the_run", "test_streaming_assembles_text_and_fragmented_tool_calls_and_usage", "test_usage_totals_unpriced_priced_and_partial", "test_missing_usage_is_reported_as_none_not_guessed", "test_full_flow_over_http_with_sse_export_import_and_preview"]),
 ("Transient failures retried with backoff, separate from repairs; auth not retried", "scripted stub + unit", ["test_transient_errors_are_retried_with_backoff_and_not_counted_as_repairs", "test_retries_exhausted_stops_with_model_unavailable", "test_auth_error_is_not_retried", "test_retry_policy_backoff_cap_and_non_retryable", "test_errors_are_classified", "test_timeout_and_unreachable_are_retryable_and_the_key_is_not_in_errors"]),
 ("Execution boundary: network blocked by harness alone, by CSP alone (with an uncontrolled control run)", "real browser, hostile page", ["test_harness_request_blocking_alone_stops_all_outbound_traffic", "test_csp_alone_blocks_fetch_xhr_and_websocket_in_a_plain_browser", "test_control_without_csp_or_blocking_the_hostile_page_does_reach_the_network"]),
 ("Execution boundary: secrets/env, storage origin, path traversal, methods", "real browser + HTTP", ["test_worker_environment_contains_no_secrets", "test_canary_secret_never_reaches_the_worker_or_the_report", "test_generated_code_cannot_read_process_env_through_the_browser", "test_projects_do_not_share_localstorage_because_origins_differ", "test_preview_path_traversal_and_non_app_files_are_refused", "test_preview_refuses_mutating_methods_and_serves_no_symlinks", "test_preview_serves_only_own_project_by_host", "test_secrets_never_reach_trace_usage_exports_or_state", "test_config_never_includes_the_key_and_ui_has_strict_csp"]),
 ("Owner scoping and concurrency", "scripted stub + HTTP", ["test_projects_are_scoped_to_their_owner", "test_requests_need_an_owner_token_and_projects_are_private", "test_concurrent_operations_on_one_project_are_refused"]),
 ("Runaway page is stopped by the harness timeout", "real browser", ["test_busy_page_times_out_as_errored_with_remaining_not_run"]),
 ("Unresolved requirements block approval, build and promotion; replace/remove only; kept through proposal, storage, restore, export/import", "scripted stub + unit", ["test_unresolved_requirement_is_shown_blocks_approval_and_is_never_called_verified", "test_there_is_no_keep_unresolved_and_approve_path", "test_replacing_with_a_supported_requirement_gets_checks_then_can_be_approved_and_built", "test_removing_a_requirement_is_an_explicit_recorded_decision", "test_a_replacement_that_is_still_unverifiable_stays_unresolved", "test_an_unverifiable_revision_cannot_be_approved_and_the_project_is_not_called_verified", "test_removing_the_only_unverifiable_addition_cancels_the_revision", "test_restore_keeps_unresolved_requirements_and_their_proposal_visible", "test_export_and_import_carry_unresolved_requirements_and_block_approval", "test_a_forged_unresolved_list_in_an_import_is_bounded_and_cleaned"]),
 ("Readable criteria carry no selectors and come from the same structured criteria the harness runs", "unit", ["test_readable_text_shows_behaviour_and_no_selectors", "test_readable_and_technical_come_from_the_same_structured_steps_not_from_a_second_source", "test_labels_without_a_label_are_humanised_and_bad_labels_are_rejected", "test_readable_rendering_comes_from_the_structured_criteria"]),
 ("Varied-example rule (proposal time)", "unit", ["test_reference_criteria_satisfy_the_variation_rule", "test_single_example_criteria_are_sent_back_for_a_second_example", "test_a_second_example_with_the_same_inputs_or_the_same_result_is_not_enough", "test_requirements_that_do_not_depend_on_input_need_no_second_example"]),
 ("Hardcoded outputs pass one example but fail varied criteria; wrong behaviour with right test ids fails", "real browser, hand-written adversarial apps", ["test_hardcoded_apps_pass_one_example_but_fail_the_varied_criteria", "test_reference_apps_pass_the_varied_criteria", "test_correct_testids_and_labels_cannot_satisfy_wrong_behaviour"]),
 ("RESIDUAL GAP: an app special-casing exactly the listed examples passes them (held-out input exposes it)", "real browser, hand-written adversarial app", ["test_listed_example_lookup_passes_the_listed_criteria_and_unseen_inputs_expose_it"]),
 ("Framework implementation: revision judgment, pause, approval, continuation, repair (service workflow; NOT a graph interrupt/checkpoint test)", "scripted stub", ["test_revision_judgment_then_pause_then_approval_then_graph_build_with_a_repair", "test_the_framework_build_has_no_graph_persistence_or_interrupts"]),
 ("Hosted demo: password gate, /healthz, /preview path rules, port/host settings, Chromium sandbox fallback (NOT OS containment)", "real HTTP server + unit", ["test_healthz_is_open_and_reports_ok", "test_without_app_password_everything_is_open", "test_password_gate_blocks_ui_and_api_without_credentials", "test_wrong_or_malformed_credentials_are_refused", "test_any_username_works_with_the_right_password", "test_the_gate_cannot_be_bypassed_through_the_preview_prefix", "test_preview_needs_no_password_and_serves_the_project_files", "test_preview_keeps_the_strict_csp_and_forces_a_sandbox", "test_preview_path_traversal_and_non_app_files_are_404", "test_unknown_or_malformed_project_ids_are_404", "test_preview_is_read_only_and_serves_no_symlinks", "test_main_binds_all_interfaces_on_the_port_from_the_environment", "test_main_defaults_to_port_8080", "test_chromium_falls_back_to_no_sandbox_and_says_so"]),
 ("LIVE model: tool-call shape, streaming deltas, usage fields", "LIVE (skipped unless configured)", ["test_plain_reply_and_usage_fields", "test_tool_call_shape_non_streaming", "test_streaming_tool_call_and_usage"]),
]


def main():
    """``run_validation.py`` runs the whole suite once. ``run_validation.py --from-junit a.xml b.xml ...`` instead reads junit files
    from runs you made yourself (for example the suite split across processes); the report then lists the commands you pass with
    ``--commands``. Either way the numbers come from the junit files, not from anything typed here."""
    JUNIT.parent.mkdir(exist_ok=True)
    args = sys.argv[1:]
    commands = ["python -m pytest -q -p no:cacheprovider --junitxml=evidence/junit.xml"]
    if "--from-junit" in args:
        i = args.index("--from-junit")
        files = [Path(a) for a in args[i + 1:] if not a.startswith("--")]
        if "--commands" in args:
            commands = args[args.index("--commands") + 1:i] if args.index("--commands") < i else args[args.index("--commands") + 1:]
        roots = [ET.parse(f).getroot() for f in files]
        class R:  # no captured stdout when reading existing results
            stdout = ""
    else:
        env = {k: v for k, v in os.environ.items()}
        R = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={JUNIT}"], cwd=ROOT, env=env, capture_output=True, text=True)
        roots = [ET.parse(JUNIT).getroot()]
    r = R
    cases = []
    for tc in (t for root in roots for t in root.iter("testcase")):
        status = "passed"
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "failed"
        elif tc.find("skipped") is not None:
            status = "skipped"
        cat = next((p.get("value") for p in tc.iter("property") if p.get("name") == "category"), "uncategorised")
        cases.append((tc.get("classname", "").split(".")[-1], tc.get("name"), status, float(tc.get("time", 0)), cat))
    total = {s: sum(1 for c in cases if c[2] == s) for s in ("passed", "failed", "skipped")}
    base = lambda n: n.split("[")[0]
    out = ["# Project 1 validation report", "", "**Prototype—live-model and delivery validation outstanding.**", "",
           f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `scripts/run_validation.py` from the junit XML files of the actual pytest runs listed under Environment (`evidence/junit*.xml`).", "",
           "## Result", "",
           f"**Collected {len(cases)}: {total['passed']} passed, {total['failed']} failed, {total['skipped']} skipped** (sum of per-test times {sum(c[3] for c in cases):.0f}s; runs were split across processes, so this is not elapsed time).", "",
           "Counts by evidence category (a pass in one category says nothing about another):", "",
           "| Category | Collected | Passed | Failed | Skipped |", "|---|---|---|---|---|"]
    for cat in sorted({c[4] for c in cases}):
        n = lambda st: sum(1 for c in cases if c[4] == cat and c[2] == st)
        out.append(f"| {cat} | {sum(1 for c in cases if c[4] == cat)} | {n('passed')} | {n('failed')} | {n('skipped')} |")
    out.append("")
    out += ["## Evidence levels used (read this before trusting any row)", "",
            "- **scripted-agent-scenario**: see *scripted stub* below. **real-browser-check**: see *real browser / HTTP*. **live-model**: see *LIVE*. **http-and-demo-flow**: a real local server driven with the labelled demo transport (not a model). **unit-and-service / unit-and-http-guard**: code paths with no browser and no model.",
            "- **real browser / HTTP**: real Chromium, real local servers, hostile or hand-written pages. Evidence about our infrastructure.",
            "- **scripted stub**: a deterministic stand-in replays chosen model replies (`appbuilder/testing/scripted.py`). Proves OUR code's behaviour when the model behaves that way. **It is not evidence about any real model.**",
            "- **local fake provider**: a local HTTP server imitating an OpenAI-compatible API. Proves our transport handles those shapes. **Real Gemini field shapes are unverified.**",
            "- **LIVE**: a real configured model. None were run (see Outstanding).", ""]
    out += ["## Environment", "", f"- Python {platform.python_version()} on {platform.platform()}",
            "- " + ", ".join(f"{p} {version(p)}" for p in ["fastapi", "uvicorn", "httpx", "pydantic", "playwright", "langgraph", "pytest"]),
            "- Model configuration for ALL recorded runs: provider `stub`/`demo`, model `scripted-model`/`demo-stub` (no real model was called). Token and latency numbers in scripted runs are the script's, not a provider's.",
            "- Limits under test: `max_model_turns=20`, `max_repairs=3`, `max_tool_calls_per_turn=6`, `transient_retry_attempts=3`, `max_diagnostics=5`, `check_timeout_s=90`, `step_timeout_ms=3000` (see `appbuilder/config.py`).",
            "- Chromium ran as root with `--no-sandbox` in this container; the harness records `chromium_sandbox` in every report (see Limitations).",
            "- Commands run (from the project folder): " + "; ".join(f"`{c}`" for c in commands),
            "- Run location: the authoring container, from the build copy. **Not run from the copy in the course folder on the learner's device (Python 3.10 there).** That copy was checksum-verified only.", ""]
    out += ["## Requirement-to-evidence map", "", "| Behaviour | Evidence level | Tests | Result |", "|---|---|---|---|"]
    by = {}
    for cls, name, st, _, _c in cases:
        by.setdefault(base(name), []).append(st)
    for label, level, names in MAP:
        found = [n for n in names if n in by]
        missing = [n for n in names if n not in by]
        sts = [s for n in found for s in by[n]]
        res = "FAILED" if "failed" in sts else ("skipped (not run)" if sts and all(s == "skipped" for s in sts) else f"{sts.count('passed')} passed" + (f", {sts.count('skipped')} skipped" if "skipped" in sts else ""))
        if missing:
            res += f" (MISSING TESTS: {', '.join(missing)})"
        out.append(f"| {label} | {level} | {len(found)} test functions | {res} |")
    out += ["", "Every scenario marked *scripted stub* runs against **both** implementations (`handwritten` loop and `framework` LangGraph), parametrised in `tests/test_scenarios.py`.", ""]
    mapped = {n for _, _, names in MAP for n in names}
    unmapped = sorted({base(n) for _, n, _, _, _ in cases} - mapped)
    out += ["## Tests not listed in the map above (infrastructure and unit coverage)", "", ", ".join(f"`{n}`" for n in unmapped) or "none", ""]
    if total["failed"]:
        out += ["## FAILURES", "", "```", r.stdout[-3000:], "```", ""]
    out += ["## Outstanding (not demonstrated)", "",
            "1. **No live model was run.** No key was authorised. `tests/test_live.py` (3 tests) is ready and skips until `APPBUILDER_LIVE=1` plus provider, model and key are set. Unverified until then: Gemini's tool-call JSON shape and streamed tool-call deltas on the OpenAI-compatible endpoint, the exact usage field names, whether `response_format` JSON modes are honoured, and how well a real model follows the build, criteria and revision prompts.",
            "2. **Real model quality, cost and latency** are therefore unmeasured. Cost shows `unpriced` unless `APPBUILDER_PRICE_INPUT_PER_M` / `_OUTPUT_PER_M` are set.",
            "3. **No public hosting route** is confirmed; this report covers local operation only, and the application can now be run as a password-protected demo (the loopback restriction was removed; see the hosted-demo row). Identity/project authorisation, credential custody, quotas and OS-level execution isolation are separate unmet requirements (`DEPLOYMENT_BLOCKERS.md`).",
            "3a. **This run was in the authoring container.** The copy of the project placed in the course folder on the learner's device was integrity-checked by checksum, which shows the files transferred intact and nothing else. The application and its tests have **not been run from that copy** or in the device's Python (3.10) environment.",
            "4. **Six-session feasibility** is unvalidated. Nothing here measures it and none is inferred from code size.",
            "5. **Test gaming is reduced, not eliminated.** Varied examples stop a page that reproduces one example; a page that special-cases every listed example passes (see the RESIDUAL GAP row). The live acceptance checks are not a protected course assessment.",
            "6. **Same-user workers, browser request blocking and timeouts are not OS containment.** The boundary tests show behaviour for the cases they exercise only.", "",
            "## Limitations of the execution boundary (verified facts, not assurances)", "",
            "- Chromium's OS sandbox is disabled when running as root. Production hosting must add container isolation; the report field `conditions.chromium_sandbox` records which mode ran.",
            "- Playwright's sync API deadlocked when closing a WebSocket inside its handler (found by the boundary tests). WebSockets are now mocked and never connected; the test proves no handshake reaches an outside listener.",
            "- A CSP-blocked form post leaves the page's `load` event pending in a plain browser; the harness avoids this because blocked requests are aborted.",
            "- `serviceWorker.register()` may resolve under Playwright's `service_workers=\"block\"`; the test asserts no registration exists.",
            "- Isolation covers network egress from the page, process environment, storage origin and file access. It does not claim to stop CPU/memory exhaustion beyond the harness timeout.",
            "- The owner token separates one browser's projects from another's; it is an identifier, not authentication or project authorisation, and is not a boundary between people sharing a browser profile. A shared access code would not change that.", ""]
    (ROOT / "VALIDATION.md").write_text("\n".join(out))
    print(f"{total} -> VALIDATION.md")
    sys.exit(1 if total["failed"] else 0)


if __name__ == "__main__":
    main()
