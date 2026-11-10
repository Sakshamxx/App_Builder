"""LIVE model checks. Skipped unless APPBUILDER_LIVE=1 AND a live provider is configured (APPBUILDER_PROVIDER, APPBUILDER_MODEL, key).
These are the only tests that count as evidence about a real model. They record what they observed in evidence/live-*.json."""
import json
import os
from pathlib import Path

import pytest

from appbuilder.config import Settings
from appbuilder.supplied.model_transport import make_transport

SETTINGS = Settings.from_env()
LIVE = os.environ.get("APPBUILDER_LIVE") == "1" and SETTINGS.live_model_configured()
pytestmark = [pytest.mark.live, pytest.mark.skipif(not LIVE, reason="live model not configured/authorised (set APPBUILDER_LIVE=1 and provider, model, key)")]
EVID = Path(__file__).resolve().parents[1] / "evidence"
TOOLS = [{"type": "function", "function": {"name": "list_files", "description": "list", "parameters": {"type": "object", "properties": {}}}}]


def record(name, data):
    EVID.mkdir(exist_ok=True)
    (EVID / f"live-{name}.json").write_text(json.dumps({"provider": SETTINGS.provider, "model": SETTINGS.model, **data}, indent=2))


def test_plain_reply_and_usage_fields():
    r = make_transport(SETTINGS).chat([{"role": "user", "content": "Reply with the single word: ok"}])
    record("plain", {"content": r.content, "usage": r.usage, "finish_reason": r.finish_reason})
    assert r.content and r.usage is not None


def test_tool_call_shape_non_streaming():
    r = make_transport(SETTINGS).chat([{"role": "user", "content": "Call the list_files tool."}], tools=TOOLS)
    record("tool-call", {"tool_calls": r.tool_calls, "usage": r.usage})
    assert r.tool_calls and r.tool_calls[0]["name"] == "list_files" and json.loads(r.tool_calls[0]["arguments"]) == {}


def test_streaming_tool_call_and_usage():
    deltas = []
    r = make_transport(SETTINGS).chat([{"role": "user", "content": "Say hello, then call the list_files tool."}], tools=TOOLS, on_text=deltas.append)
    record("stream", {"deltas": len(deltas), "tool_calls": r.tool_calls, "usage": r.usage})
    assert r.tool_calls and r.usage is not None
