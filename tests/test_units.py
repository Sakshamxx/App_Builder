"""Small unit tests: settings, usage/cost accounting, redaction, retry policy, structured parsing, status derivation, contracts."""
import json

import pytest
from pydantic import ValidationError

from appbuilder.agent.contracts import RevisionJudgment, ScopeAssessment
from appbuilder.agent.retry import call_with_retry
from appbuilder.agent.structured import extract_json
from appbuilder.agent.trace import Tracer
from appbuilder.agent.usage import UsageLedger
from appbuilder.config import Limits, Settings
from appbuilder.supplied.model_transport import TransportError
from appbuilder.supplied.redact import MASK, Redactor
from tests.fixtures_data import scope


def test_settings_from_env_defaults_and_providers():
    s = Settings.from_env({"APPBUILDER_PROVIDER": "gemini", "APPBUILDER_MODEL": "m", "GEMINI_API_KEY": "AIzaXXXXXXXXXXXXXXXXXXXX"})
    assert s.base_url.startswith("https://generativelanguage.googleapis.com/v1beta/openai") and s.live_model_configured()
    assert s.structured_mode == "prompt_only" and s.limits.max_model_turns == 20 and s.limits.max_repairs == 3
    assert "AIzaXXXXXXXXXXXXXXXXXXXX" in s.secrets() and "AIza" not in repr(s)          # key excluded from repr
    o = Settings.from_env({"APPBUILDER_PROVIDER": "ollama", "APPBUILDER_MODEL": "llama3"})
    assert o.base_url == "http://localhost:11434/v1/" and o.live_model_configured()
    assert not Settings.from_env({}).live_model_configured()                              # no model id is guessed


def test_usage_totals_unpriced_priced_and_partial(tmp_path):
    led = UsageLedger(tmp_path / "u.jsonl")
    led.record("a", "m", {"prompt_tokens": 1000, "completion_tokens": 500})
    assert led.totals()["cost"]["status"] == "unpriced"
    priced = UsageLedger(None, 2.0, 10.0)
    priced.record("a", "m", {"prompt_tokens": 1_000_000, "completion_tokens": 100_000})
    assert priced.totals()["cost"] == {"status": "priced", "amount": 3.0, "note": "at configured prices"}
    priced.record("b", "m", None)                                                         # provider reported nothing: never estimated
    t = priced.totals()
    assert t["calls_without_usage"] == 1 and t["cost"]["status"] == "partial" and t["prompt_tokens"] == 1_000_000
    assert UsageLedger.load(tmp_path / "u.jsonl").totals()["prompt_tokens"] == 1000


def test_redactor_masks_configured_and_common_key_shapes():
    r = Redactor(["my-secret-value-123"])
    out = r.obj({"a": "x my-secret-value-123 y", "b": ["AIzaSyDabcdefghijklmnopqrstuvwxyz0123", "Bearer abcdefghijklmnopqrstuv"], "c": 5})
    assert out == {"a": f"x {MASK} y", "b": [MASK, MASK], "c": 5}


def test_tracer_redacts_before_file_and_sink(tmp_path):
    seen = []
    t = Tracer(tmp_path / "t.jsonl", Redactor(["supersecretkey1"]), seen.append)
    t.event("x", note="has supersecretkey1 inside")
    t.live("model_text", delta="supersecretkey1")
    assert "supersecretkey1" not in (tmp_path / "t.jsonl").read_text() and all("supersecretkey1" not in json.dumps(e) for e in seen)


def test_retry_policy_backoff_cap_and_non_retryable(tmp_path):
    L = Limits(transient_retry_attempts=4)
    tr = Tracer(None, Redactor())
    sleeps, n = [], {"i": 0}
    def flaky():
        n["i"] += 1
        if n["i"] < 4:
            raise TransportError("server", "x")
        return "ok"
    assert call_with_retry(flaky, L, tr, sleep=sleeps.append) == "ok" and sleeps == [1.0, 2.0, 4.0]
    with pytest.raises(TransportError):
        call_with_retry(lambda: (_ for _ in ()).throw(TransportError("auth", "x")), L, tr, sleep=sleeps.append)
    assert sleeps == [1.0, 2.0, 4.0]                                                      # auth: no retry, no sleep


@pytest.mark.parametrize("raw,expected", [('{"a": 1}', {"a": 1}), ('```json\n{"a": 1}\n```', {"a": 1}), ('Sure! {"a": 1} hope that helps', {"a": 1})])
def test_extract_json(raw, expected):
    assert extract_json(raw) == expected


def test_contracts_reject_bad_scope_and_conflict_without_question():
    ok = scope("tip_calculator")
    ScopeAssessment.model_validate(ok)
    for mutate in (lambda d: d.update(pages=[]), lambda d: d.update(pages=[{"filename": "a.html", "purpose": "x"}] * 4),
                   lambda d: d.update(pages=[{"filename": "Home.html", "purpose": "x"}]), lambda d: d.update(requirements=[]),
                   lambda d: d["requirements"].append(d["requirements"][0]), lambda d: d.update(supported=False, unsupported=[])):
        bad = json.loads(json.dumps(ok)); mutate(bad)
        with pytest.raises(ValidationError):
            ScopeAssessment.model_validate(bad)
    with pytest.raises(ValidationError):
        RevisionJudgment(verdict="conflict", explanation="x")


def test_a_diagnostic_report_is_never_current_even_if_its_hashes_match(tmp_path):
    """Defence in depth: the loop's notion of 'current trusted report' requires kind == acceptance, not just matching hashes."""
    from appbuilder.agent.loop import is_current
    from appbuilder.agent.tools import BuildState
    from appbuilder.supplied.harness import run_checks
    from appbuilder.supplied.store import ProjectStore
    from appbuilder.agent.context import RunContext
    from tests.fixtures_data import copy_app, criteria, scope
    store = ProjectStore(tmp_path / "d")
    pid = store.create("owner-aaaaaaaaaaaaaaaa", "t")
    copy_app("tip_calculator", store.app_dir(pid))
    store.approve_contract(pid, scope("tip_calculator"), criteria("tip_calculator"))
    ctx = RunContext(settings=Settings(data_dir=tmp_path), store=store, pid=pid, transport=None, tracer=Tracer(None, Redactor()), usage=UsageLedger())
    rep = run_checks(store.app_dir(pid), criteria("tip_calculator"), Limits(), kind="acceptance")
    st = BuildState(last_report=rep)
    assert is_current(ctx, st)
    st.last_report = {**rep, "kind": "diagnostic"}            # same hashes, same results, wrong kind
    assert not is_current(ctx, st)
