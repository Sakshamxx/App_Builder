"""Scenario helpers: build a service on a scripted stub model and drive it through the user's approval gates."""
from __future__ import annotations

from appbuilder.agent.service import AppBuilderService
from appbuilder.config import Limits, Settings
from appbuilder.testing.scripted import ScriptedTransport, calls, js, text, write
from tests.fixtures_data import EXPENSE_BUG, criteria, read_app, scope

OWNER = "owner-aaaaaaaaaaaaaaaa"
OWNER2 = "owner-bbbbbbbbbbbbbbbb"


def make_service(tmp_path, impl, script=(), *, limits: Limits | None = None, api_key=None, loop_last=False, run_checks=None):
    settings = Settings(data_dir=tmp_path / "data", limits=limits or Limits(), provider="stub", model="scripted-model",
                        implementation=impl, api_key=api_key)
    transport = ScriptedTransport(script, loop_last=loop_last)
    sleeps: list[float] = []
    svc = AppBuilderService(settings, transport_factory=lambda s: transport, sleep=sleeps.append, run_checks=run_checks)
    svc.test_transport, svc.test_sleeps = transport, sleeps
    return svc


def scope_reply(name):
    return js(scope(name))


def criteria_reply(name, unverifiable=()):
    return js({**criteria(name), "unverifiable": list(unverifiable)})


def write_all(name, mutate=None):
    files = read_app(name, mutate)
    return calls(*[write(f, c) for f, c in files.items()])


RUN = calls(("run_checks", {}))


def drive_to_build(svc, request="build it", owner=OWNER):
    """create -> approve scope -> approve criteria (which starts the build). Returns the final state."""
    st = svc.create(owner, request)
    st = svc.approve_scope(owner, st["id"], st["pending"]["scope_hash"])
    return svc.approve_criteria(owner, st["id"], st["pending"]["criteria_hash"])


def new_verified(tmp_path, impl, name="expense_tracker", **kw):
    """A service with a verified project built from known-good files (scripted: write everything, run checks)."""
    script = [scope_reply(name), criteria_reply(name), write_all(name), RUN]
    svc = make_service(tmp_path, impl, script, **kw)
    st = drive_to_build(svc)
    assert st["status"]["status"] == "verified", st["last_build"]
    return svc, st["id"]
