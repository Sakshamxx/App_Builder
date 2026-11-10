"""Tags every test with an evidence category so reports can count them separately (written into the junit XML).

collected / passed / failed / skipped are reported per category by ``scripts/run_validation.py``. The categories describe what KIND
of evidence a test gives; a pass in one category says nothing about another (a scripted-agent pass is not live-model evidence).
"""
import pytest

CATEGORY_BY_FILE = {
    "test_scenarios.py": "scripted-agent-scenario",          # stub model replays chosen replies; both implementations
    "test_unresolved.py": "scripted-agent-scenario",
    "test_framework_workflow.py": "scripted-agent-scenario",
    "test_harness.py": "real-browser-check",                 # real Chromium against hand-written apps
    "test_acceptance_behaviour.py": "real-browser-check",
    "test_boundaries.py": "real-browser-check",
    "test_ui.py": "real-browser-check",
    "test_server.py": "http-and-demo-flow",                   # real HTTP server, demo transport (not a model)
    "test_deploy.py": "unit-and-http-guard",
    "test_transport.py": "local-fake-provider",              # local HTTP server imitating an OpenAI-compatible API
    "test_supplied_services.py": "unit-and-service",
    "test_units.py": "unit-and-service",
    "test_live.py": "live-model",                            # skipped unless a provider is configured
}


def pytest_collection_modifyitems(items):
    for item in items:
        cat = CATEGORY_BY_FILE.get(item.path.name, "uncategorised")
        item.user_properties.append(("category", cat))
