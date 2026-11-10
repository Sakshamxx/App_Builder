"""DEMO MODE ONLY. A scripted stand-in that lets the whole application be clicked through without any API key.

It is NOT a model. It recognises three example requests (tip calculator, habit tracker, expense tracker) by keyword and replays
hand-written scope, criteria and app files. Anything else is answered as 'unsupported' with an explanation. The UI shows a permanent
DEMO banner while this is active, and nothing produced in demo mode counts as live-model evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..supplied.model_transport import ModelReply

FIX = Path(__file__).resolve().parents[2] / "tests"


def _fixtures():
    import sys
    sys.path.insert(0, str(FIX.parent))
    from tests import fixtures_data as fd
    return fd


KEYWORDS = {"tip": "tip_calculator", "habit": "habit_tracker", "expense": "expense_tracker", "budget": "expense_tracker"}


def _reply(content=None, calls=None):
    return ModelReply(content, calls or [], "stop" if not calls else "tool_calls", {"prompt_tokens": 50, "completion_tokens": 20}, "demo-stub")


class DemoTransport:
    def __init__(self):
        self.fd = _fixtures()

    def _which(self, text: str) -> str | None:
        t = text.lower()
        return next((v for k, v in KEYWORDS.items() if k in t), None)

    def chat(self, messages, tools=None, *, on_text=None, response_format=None, temperature=0.0):
        reply = self._chat(messages)
        if on_text and reply.content:                      # stream in small chunks like a real provider would
            for i in range(0, len(reply.content), 16):
                on_text(reply.content[i:i + 16])
        return reply

    def _chat(self, messages):
        system, last = messages[0]["content"], messages[-1]["content"]
        full = " ".join(m.get("content") or "" for m in messages if isinstance(m.get("content"), str))
        if system.startswith("You assess whether a request"):
            app = self._which(last)
            if app:
                out = self.fd.scope(app)
            else:
                out = {"supported": False, "app_title": "Not available in demo", "summary": "demo", "pages": [], "requirements": [],
                       "unsupported": [{"feature": "anything outside the three demo examples",
                                        "reason": "Demo mode only knows a tip calculator, a habit tracker and an expense tracker."}],
                       "proposed_alternative": "A tip calculator that splits a bill and remembers the last tip percentage."}
            return _reply(json.dumps(out))
        if system.startswith("You turn approved requirements"):
            app = self._which(last)
            return _reply(json.dumps({**self.fd.criteria(app), "unverifiable": []}))
        if system.startswith("You judge a requested change"):
            return _reply(json.dumps({"verdict": "unsupported", "explanation": "Demo mode cannot revise apps; connect a model to try revisions.",
                                      "touched_requirement_ids": [], "added_requirements": [], "conflicting_requirement_ids": [],
                                      "clarification_question": None}))
        # build loop: write every file once, then run the checks
        app = self._which(system) or "tip_calculator"
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        if not tool_msgs:
            files = self.fd.read_app(app)
            calls = [{"id": f"c{i}", "name": "write_file", "arguments": json.dumps({"path": n, "content": c})} for i, (n, c) in enumerate(files.items())]
            return _reply("Writing the app files (demo stub).", calls)
        if len(tool_msgs) <= len(self.fd.read_app(app)):
            return _reply(None, [{"id": "check", "name": "run_checks", "arguments": "{}"}])
        return _reply("Done (demo stub).")
