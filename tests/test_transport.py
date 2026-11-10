"""The thin transport against a local fake OpenAI-compatible server (NOT a live provider: it only proves our
request/response handling; real Gemini field shapes remain a live-verification item)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from appbuilder.supplied.model_transport import OpenAICompatTransport, TransportError

KEY = "AIzaFAKEKEY0123456789abcdefghij"


class Fake:
    def __init__(self):
        self.requests = []
        self.mode = "ok"
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                m = outer.mode
                if m == "429":
                    self.send_response(429); self.send_header("Retry-After", "2"); self.end_headers(); self.wfile.write(b"quota"); return
                if m == "401":
                    self.send_response(401); self.end_headers(); self.wfile.write(b"bad key " + KEY.encode()); return
                if m == "500":
                    self.send_response(503); self.end_headers(); self.wfile.write(b"down"); return
                if m == "garbage":
                    self.send_response(200); self.end_headers(); self.wfile.write(b"{}"); return
                if m == "slow":
                    time.sleep(2); return
                if body.get("stream"):
                    self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                    ev = [{"model": "m", "choices": [{"delta": {"content": "Hel"}}]},
                          {"choices": [{"delta": {"content": "lo"}}]},
                          {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "write_file", "arguments": "{\"path\":"}}]}}]},
                          {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"a.html\"}"}}]}}]},
                          {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "c2", "function": {"name": "list_files", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]},
                          {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}]
                    for e in ev:
                        self.wfile.write(f"data: {json.dumps(e)}\n\n".encode()); self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n"); return
                out = {"model": "m", "choices": [{"finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}]}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
                data = json.dumps(out).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1beta/openai/"


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.httpd.shutdown()


def T(fake, **kw):
    return OpenAICompatTransport(fake.base, KEY, "test-model", **kw)


def test_non_streaming_reply_tool_calls_and_usage(fake):
    r = T(fake).chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "list_files"}}])
    assert r.tool_calls == [{"id": "c1", "name": "list_files", "arguments": "{}"}]
    assert r.usage == {"prompt_tokens": 5, "completion_tokens": 3} and r.finish_reason == "tool_calls"
    req = fake.requests[0]
    assert req["path"] == "/v1beta/openai/chat/completions" and req["auth"] == f"Bearer {KEY}"
    assert req["body"]["model"] == "test-model" and "stream" not in req["body"]
    assert r.assistant_message()["tool_calls"][0]["function"]["name"] == "list_files"


def test_streaming_assembles_text_and_fragmented_tool_calls_and_usage(fake):
    got = []
    r = T(fake).chat([{"role": "user", "content": "hi"}], on_text=got.append)
    assert "".join(got) == "Hello" == r.content
    assert r.tool_calls == [{"id": "c1", "name": "write_file", "arguments": '{"path":"a.html"}'},
                            {"id": "c2", "name": "list_files", "arguments": "{}"}]
    assert r.usage == {"prompt_tokens": 11, "completion_tokens": 7}
    assert fake.requests[0]["body"]["stream"] is True


@pytest.mark.parametrize("mode,kind,retryable", [("429", "rate_limit", True), ("401", "auth", False), ("500", "server", True),
                                                 ("garbage", "bad_response", False)])
def test_errors_are_classified(fake, mode, kind, retryable):
    fake.mode = mode
    with pytest.raises(TransportError) as e:
        T(fake).chat([{"role": "user", "content": "x"}])
    assert e.value.kind == kind and e.value.retryable is retryable
    if mode == "429":
        assert e.value.retry_after == 2.0


def test_timeout_and_unreachable_are_retryable_and_the_key_is_not_in_errors(fake):
    fake.mode = "slow"
    with pytest.raises(TransportError) as e:
        T(fake, timeout_s=0.5).chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "timeout" and e.value.retryable
    with pytest.raises(TransportError) as e:
        OpenAICompatTransport("http://127.0.0.1:9/v1/", KEY, "m", timeout_s=1).chat([{"role": "user", "content": "x"}])
    assert e.value.kind == "network" and e.value.retryable


def test_the_key_never_appears_in_repr_or_error_text(fake):
    t = T(fake)
    assert KEY not in repr(t)
    fake.mode = "401"
    with pytest.raises(TransportError) as e:
        t.chat([{"role": "user", "content": "x"}])
    assert KEY not in e.value.message   # auth errors do not echo the response body


def test_missing_usage_is_reported_as_none_not_guessed(fake):
    # a provider that omits usage must not be given invented numbers
    t = T(fake)
    r = t._parse({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    assert r.usage is None and r.content == "ok"
