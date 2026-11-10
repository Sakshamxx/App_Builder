"""TEST SUPPORT. A deterministic stand-in for a model, used to exercise failure paths before live model testing.

It replays a script of replies. It is NOT evidence about any real model's behaviour: scenario results produced with it
are labelled 'scripted stub' in the validation report.
"""
from __future__ import annotations

import json

from ..supplied.model_transport import ModelReply, TransportError


def text(content: str, usage=(100, 20)) -> ModelReply:
    return ModelReply(content, [], "stop", {"prompt_tokens": usage[0], "completion_tokens": usage[1]}, "scripted-model")


def js(obj, usage=(100, 20)) -> ModelReply:
    return text(json.dumps(obj), usage)


def calls(*specs, usage=(200, 50), content=None) -> ModelReply:
    """specs: (tool_name, args_dict) tuples."""
    tc = [{"id": f"call_{i}", "name": n, "arguments": json.dumps(a)} for i, (n, a) in enumerate(specs)]
    return ModelReply(content, tc, "tool_calls", {"prompt_tokens": usage[0], "completion_tokens": usage[1]}, "scripted-model")


def write(path: str, content: str):
    return ("write_file", {"path": path, "content": content})


class ScriptedTransport:
    """Each item is a ModelReply, a TransportError (raised), or a callable(messages, tools) -> ModelReply/Error."""

    def __init__(self, script, *, loop_last: bool = False):
        self.script, self.i, self.loop_last = list(script), 0, loop_last
        self.requests: list[dict] = []

    def chat(self, messages, tools=None, *, on_text=None, response_format=None, temperature=0.0):
        self.requests.append({"messages": json.loads(json.dumps(messages)), "tools": bool(tools), "stream": on_text is not None})
        if self.i >= len(self.script):
            if self.loop_last and self.script:
                item = self.script[-1]
            else:
                raise AssertionError(f"script exhausted after {self.i} calls")
        else:
            item = self.script[self.i]
        self.i += 1
        if callable(item):
            item = item(messages, tools)
        if isinstance(item, Exception):
            raise item
        if on_text and item.content:
            for ch in [item.content[i:i + 12] for i in range(0, len(item.content), 12)]:
                on_text(ch)
        return item
