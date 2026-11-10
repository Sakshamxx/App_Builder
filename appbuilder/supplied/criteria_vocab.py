"""SUPPLIED. The bounded vocabulary for acceptance criteria, its validator and its readable rendering.

The harness interprets these structured steps. The agent never writes acceptance-test code, and an
assertion outside this vocabulary is rejected with an explicit message, never silently dropped.

Targets are ``data-testid`` values. They are an INTERNAL convention between the criteria and the generated app: the readable
text a user approves (``render_readable``) never shows them. It describes behaviour using plain labels (the optional top-level
``labels`` map, or a humanised form of the testid). ``render_technical`` keeps the selectors and is for the builder only.
Both renderings are generated from the same structured criteria the harness executes; nothing is written twice.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..config import Limits

TESTID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
PAGE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}\.html$")
CID_RE = re.compile(r"^C[0-9]{1,2}$")

# action -> (required fields, optional fields)
ACTIONS: dict[str, tuple[set[str], set[str]]] = {
    "navigate": ({"page"}, set()),
    "enter": ({"target", "value"}, set()),
    "select": ({"target", "value"}, set()),
    "filter": ({"target", "value"}, set()),
    "click": ({"target"}, set()),
    "reload": (set(), set()),
    "assert_text": ({"target"}, {"equals", "contains"}),   # exactly one of equals / contains
    "assert_count": ({"target", "count"}, set()),
    "assert_visible": ({"target"}, set()),
}

VOCABULARY_HELP = """Supported steps (nothing else is accepted):
- navigate {page}                      open one of the app's pages
- enter {target, value}                type a value into an input (target = data-testid)
- select {target, value}               choose an option in a dropdown
- filter {target, value}               set a filter control (input or dropdown)
- click {target}                       click a button or link
- reload {}                            reload the current page (tests browser-local persistence)
- assert_text {target, equals|contains}  the element's visible text equals / contains a string
- assert_count {target, count}         the number of elements with that data-testid
- assert_visible {target}              the element is visible
Each step is a JSON object: "action" holds the step name and the fields in braces are its other keys, for example
{"action": "navigate", "page": "index.html"}, {"action": "enter", "target": "bill-amount", "value": "50"},
{"action": "assert_text", "target": "tip-amount", "equals": "7.50"}, {"action": "assert_count", "target": "expense-item", "count": 2}.
Top level: {"fixed_time": "2026-11-15T10:00:00" (optional), "labels": {"month-total": "Monthly total", ...} (optional plain
names for the testids, shown to the user instead of the testids), "criteria": [{"id": "C1", "title": "...",
"requirement_ids": ["R1"], "steps": [...]}]}. Every criterion starts with a navigate step."""


def validate_criteria(doc: object, pages: list[str], limits: Limits) -> list[str]:
    """Return a list of human-readable problems. Empty list means valid."""
    problems: list[str] = []
    if not isinstance(doc, dict):
        return ["criteria must be an object"]
    extra = set(doc) - {"fixed_time", "criteria", "labels"}
    if extra:
        problems.append(f"unknown top-level fields: {sorted(extra)}")
    problems += _validate_labels(doc.get("labels"))
    ft = doc.get("fixed_time")
    if ft is not None:
        try:
            datetime.fromisoformat(str(ft))
        except ValueError:
            problems.append(f"fixed_time {ft!r} is not an ISO date/time")
    crit = doc.get("criteria")
    if not isinstance(crit, list) or not crit:
        return problems + ["criteria must be a non-empty list"]
    if len(crit) > limits.max_criteria:
        problems.append(f"at most {limits.max_criteria} criteria")
    seen: set[str] = set()
    for ci, c in enumerate(crit):
        where = f"criterion #{ci + 1}"
        if not isinstance(c, dict):
            problems.append(f"{where}: must be an object")
            continue
        cid = c.get("id")
        if not isinstance(cid, str) or not CID_RE.match(cid):
            problems.append(f"{where}: id must look like C1, C2, ...")
        elif cid in seen:
            problems.append(f"{where}: duplicate id {cid}")
        else:
            seen.add(cid)
        where = f"criterion {cid or ci + 1}"
        if not isinstance(c.get("title"), str) or not c["title"].strip():
            problems.append(f"{where}: title is required")
        rids = c.get("requirement_ids")
        if not isinstance(rids, list) or not rids or not all(isinstance(r, str) for r in rids):
            problems.append(f"{where}: requirement_ids must be a non-empty list of strings")
        steps = c.get("steps")
        if not isinstance(steps, list) or not steps:
            problems.append(f"{where}: steps must be a non-empty list")
            continue
        if len(steps) > limits.max_steps_per_criterion:
            problems.append(f"{where}: at most {limits.max_steps_per_criterion} steps")
        if not (isinstance(steps[0], dict) and steps[0].get("action") == "navigate"):
            problems.append(f"{where}: the first step must be navigate")
        for si, s in enumerate(steps):
            problems += _validate_step(f"{where} step {si + 1}", s, pages, limits)
    return problems


def _validate_labels(labels: object) -> list[str]:
    if labels is None:
        return []
    if not isinstance(labels, dict) or len(labels) > 60:
        return ["labels must be an object mapping a testid to a short plain name (at most 60 entries)"]
    out = []
    for k, v in labels.items():
        if not (isinstance(k, str) and TESTID_RE.match(k)):
            out.append(f"label key {k!r} must be a testid")
        elif not (isinstance(v, str) and 1 <= len(v) <= 40) or any(ch in v for ch in "[]<>_") or "testid" in v.lower() or v == k:
            out.append(f"label for {k!r} must be a plain name of at most 40 characters (not the testid, no brackets or underscores)")
    return out


def _validate_step(where: str, s: object, pages: list[str], limits: Limits) -> list[str]:
    if not isinstance(s, dict):
        return [f"{where}: must be an object"]
    action = s.get("action")
    if action not in ACTIONS:
        return [f"{where}: '{action}' is not a supported step or assertion. "
                f"Supported: {', '.join(ACTIONS)}. It cannot be checked by this harness."]
    required, optional = ACTIONS[action]
    out: list[str] = []
    allowed = required | optional | {"action"}
    for k in sorted(set(s) - allowed):
        out.append(f"{where}: unexpected field '{k}' for {action}")
    for k in sorted(required - set(s)):
        out.append(f"{where}: {action} needs '{k}'")
    if "page" in s and not (isinstance(s["page"], str) and PAGE_RE.match(s["page"]) and s["page"] in pages):
        out.append(f"{where}: page {s['page']!r} is not one of the app's pages {pages}")
    if "target" in s and not (isinstance(s["target"], str) and TESTID_RE.match(s["target"])):
        out.append(f"{where}: target must be a data-testid like 'total' (lowercase letters, digits, - or _)")
    if "value" in s and not (isinstance(s["value"], str) and len(s["value"]) <= limits.max_step_value_chars):
        out.append(f"{where}: value must be a string of at most {limits.max_step_value_chars} characters")
    if action == "assert_text":
        both = [k for k in ("equals", "contains") if k in s]
        if len(both) != 1:
            out.append(f"{where}: assert_text needs exactly one of 'equals' or 'contains'")
        for k in both:
            if not (isinstance(s[k], str) and len(s[k]) <= limits.max_step_value_chars):
                out.append(f"{where}: '{k}' must be a short string")
    if action == "assert_count":
        c = s.get("count")
        if not (isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 1000):
            out.append(f"{where}: count must be an integer from 0 to 1000")
    return out


def humanise(testid: str) -> str:
    t = testid.replace("-", " ").replace("_", " ").strip()
    return t[:1].upper() + t[1:]


def _name(doc: dict, target: str, technical: bool) -> str:
    if technical:
        return f"[{target}]"
    return (doc.get("labels") or {}).get(target) or humanise(target)


def _describe(s: dict, doc: dict, technical: bool = False) -> str:
    a, t = s["action"], (_name(doc, s["target"], technical) if "target" in s else None)
    if a == "navigate":
        return f'Open the page "{s["page"]}"'
    if a == "enter":
        return f'Enter "{s["value"]}" in {t}' if technical else f'Type "{s["value"]}" into {t}'
    if a == "select":
        return f'Choose "{s["value"]}" in {t}'
    if a == "filter":
        return f'Set the filter {t} to "{s["value"]}"'
    if a == "click":
        return f"Click {t}"
    if a == "reload":
        return "Reload the page"
    if a == "assert_text":
        verb = "to read exactly" if "equals" in s else "to contain"
        val = s["equals"] if "equals" in s else s["contains"]
        return f'Expect {t} {verb} "{val}"' if technical else f'Check that {t} {"shows exactly" if "equals" in s else "contains"} "{val}"'
    if a == "assert_count":
        return f"Expect {s['count']} element(s) {t}" if technical else f"Check that exactly {s['count']} of {t} {'is' if s['count'] == 1 else 'are'} shown"
    if a == "assert_visible":
        return f"Expect {t} to be visible" if technical else f"Check that {t} is visible"
    return str(s)


def _render(doc: dict, scope: dict | None, technical: bool) -> str:
    lines: list[str] = []
    if doc.get("fixed_time"):
        lines.append(f"Checks run with the browser clock fixed at {doc['fixed_time']}, on clean browser storage.")
    else:
        lines.append("Checks run on clean browser storage.")
    reqs = {r["id"]: r["text"] for r in (scope or {}).get("requirements", [])}
    for c in doc.get("criteria", []):
        lines.append("")
        lines.append(f"{c['id']}: {c['title']}  (covers {', '.join(c['requirement_ids'])})")
        for rid in c["requirement_ids"]:
            if rid in reqs:
                lines.append(f"    {rid}: {reqs[rid]}")
        for i, s in enumerate(c["steps"], 1):
            lines.append(f"  {i}. {_describe(s, doc, technical)}")
    return "\n".join(lines)


def render_readable(doc: dict, scope: dict | None = None) -> str:
    """What the user approves: behaviour in plain words, generated from the SAME structured criteria the harness runs.
    No selectors or testids appear. If ``scope`` is given, each criterion is shown under the requirement text it covers."""
    return _render(doc, scope, technical=False)


def render_technical(doc: dict) -> str:
    """For the builder (model prompt) and for debugging only: the same criteria with the testids the app must expose."""
    return _render(doc, None, technical=True)
