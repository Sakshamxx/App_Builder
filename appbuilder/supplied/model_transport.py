"""SUPPLIED. A thin transport for OpenAI-compatible ``/chat/completions`` endpoints.

This is the only place that speaks HTTP to a model provider. It does NOT run the agent loop, dispatch
tools, parse tool arguments, decide retries, or track cost: it sends one request and returns one
reply (or raises a classified ``TransportError``). Everything that makes the application an agent
(message flow, tool schemas, dispatch, retry policy, repair, usage accounting) is learner-owned and
lives in ``appbuilder/agent`` and ``appbuilder/framework``.

Targets: Gemini's OpenAI-compatible endpoint (beta) and Ollama's ``/v1`` endpoint. Provider-specific
field names (usage, tool-call deltas) are read defensively; anything unexpected is reported, never
guessed. The API key lives only in this object and is sent only in the Authorization header.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

import httpx


class TransportError(Exception):
    """kind: auth | rate_limit | timeout | network | server | bad_request | bad_response"""

    def __init__(self, kind: str, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.kind, self.message, self.status, self.retry_after = kind, message, status, retry_after

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate_limit", "timeout", "network", "server")


@dataclass
class ModelReply:
    content: str | None
    tool_calls: list[dict] = field(default_factory=list)   # [{"id", "name", "arguments": <raw string>, "extra_content"?}]
    finish_reason: str | None = None
    usage: dict | None = None                               # {"prompt_tokens","completion_tokens"} or None if unreported
    model: str | None = None

    def assistant_message(self) -> dict:
        """The message to append to the conversation, in the provider's wire shape."""
        m: dict = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            m["tool_calls"] = [_wire_call(c) for c in self.tool_calls]
        return m


def _wire_call(c: dict) -> dict:
    """A tool call as it is sent back to the provider. ``extra_content`` is provider data that belongs to the call and must be
    returned unchanged: Gemini puts a ``thought_signature`` there and refuses the next turn (HTTP 400) when it is missing."""
    w = {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
    if c.get("extra_content"):
        w["extra_content"] = c["extra_content"]
    return w


def _call(i: int, c: dict) -> dict:
    out = {"id": c["id"] or f"call_{i}", "name": c["name"], "arguments": c["arguments"] or "{}"}
    if c.get("extra_content"):
        out["extra_content"] = c["extra_content"]
    return out


def _classify(status: int, body: str, headers) -> TransportError:
    ra = None
    try:
        ra = float(headers.get("retry-after")) if headers.get("retry-after") else None
    except ValueError:
        pass
    snippet = body[:300].replace("\n", " ")
    if status in (401, 403):
        return TransportError("auth", f"the provider rejected the credentials (HTTP {status})", status=status)
    if status == 429:
        return TransportError("rate_limit", f"rate limit or quota reached (HTTP 429): {snippet}", status=status, retry_after=ra)
    if status == 408:
        return TransportError("timeout", "the provider timed out (HTTP 408)", status=status)
    if status >= 500:
        return TransportError("server", f"provider error (HTTP {status}): {snippet}", status=status, retry_after=ra)
    return TransportError("bad_request", f"the provider refused the request (HTTP {status}): {snippet}", status=status)


def _usage(u) -> dict | None:
    if not isinstance(u, dict):
        return None
    p, c = u.get("prompt_tokens", u.get("input_tokens")), u.get("completion_tokens", u.get("output_tokens"))
    if not isinstance(p, int) or not isinstance(c, int):
        return None
    return {"prompt_tokens": p, "completion_tokens": c}


class OpenAICompatTransport:
    def __init__(self, base_url: str, api_key: str, model: str, *, timeout_s: float = 60.0, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/") + "/"
        self.model = model
        self._key = api_key
        self._timeout = timeout_s
        self._client = client or httpx.Client(timeout=timeout_s)

    def __repr__(self) -> str:  # never show the key
        return f"OpenAICompatTransport(base_url={self.base_url!r}, model={self.model!r})"

    def _body(self, messages, tools, stream, response_format, temperature):
        body: dict = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            body["tools"] = tools
        if response_format:
            body["response_format"] = response_format
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return body

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, on_text: Callable[[str], None] | None = None,
             response_format: dict | None = None, temperature: float = 0.0) -> ModelReply:
        """One request. If ``on_text`` is given the reply is streamed and each text delta is passed to it."""
        stream = on_text is not None
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        url = self.base_url + "chat/completions"
        body = self._body(messages, tools, stream, response_format, temperature)
        try:
            if not stream:
                r = self._client.post(url, headers=headers, json=body)
                if r.status_code != 200:
                    raise _classify(r.status_code, r.text, r.headers)
                return self._parse(r.json())
            with self._client.stream("POST", url, headers=headers, json=body) as r:
                if r.status_code != 200:
                    r.read()
                    raise _classify(r.status_code, r.text, r.headers)
                return self._parse_stream(r.iter_lines(), on_text)
        except httpx.TimeoutException:
            raise TransportError("timeout", f"no response from the provider within {self._timeout:.0f}s")
        except httpx.TransportError as e:
            raise TransportError("network", f"could not reach the provider: {type(e).__name__}")

    def _parse(self, data: dict) -> ModelReply:
        try:
            ch = data["choices"][0]
            msg = ch["message"]
        except (KeyError, IndexError, TypeError):
            raise TransportError("bad_response", "the provider's reply had no choices[0].message")
        calls = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn = c.get("function") or {}
            calls.append(_call(i, {"id": c.get("id"), "name": fn.get("name", ""), "arguments": fn.get("arguments"),
                                   "extra_content": c.get("extra_content")}))
        return ModelReply(msg.get("content"), calls, ch.get("finish_reason"), _usage(data.get("usage")), data.get("model"))

    def _parse_stream(self, lines, on_text) -> ModelReply:
        text: list[str] = []
        calls: dict[int, dict] = {}
        finish = usage = model = None
        for raw in lines:
            if not raw or not raw.startswith("data:"):
                continue
            payload = raw[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except ValueError:
                raise TransportError("bad_response", "a streamed chunk was not valid JSON")
            model = ev.get("model", model)
            usage = _usage(ev.get("usage")) or usage
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content"):
                    text.append(d["content"])
                    on_text(d["content"])
                for tc in d.get("tool_calls") or []:
                    idx = tc.get("index", len(calls) if tc.get("id") else max(calls, default=0))
                    slot = calls.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                    slot["id"] = tc.get("id") or slot["id"]
                    if tc.get("extra_content"):
                        slot["extra_content"] = tc["extra_content"]
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
                finish = ch.get("finish_reason") or finish
        tool_calls = [_call(i, c) for i, c in sorted(calls.items())]
        return ModelReply("".join(text) or None, tool_calls, finish, usage, model)


def make_transport(settings, *, client: httpx.Client | None = None) -> OpenAICompatTransport:
    if settings.provider not in ("gemini", "ollama") or not settings.model or not settings.api_key:
        raise TransportError("bad_request", "no live model is configured (set APPBUILDER_PROVIDER, APPBUILDER_MODEL and the key)")
    return OpenAICompatTransport(settings.base_url, settings.api_key, settings.model, client=client)
