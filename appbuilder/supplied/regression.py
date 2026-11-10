"""SUPPLIED. Regression-runner planning.

After a revision, criteria split into *relevant* (they cover requirements the revision touches) and
*regression* (everything else, which must keep passing). Deciding WHICH requirements a revision
touches is a model judgment and stays in the learner's agent code; this module only does the
deterministic bookkeeping so reports can say which kind of check failed.
"""
from __future__ import annotations


def plan(criteria: dict, touched_requirement_ids: list[str]) -> dict[str, str]:
    touched = set(touched_requirement_ids)
    return {c["id"]: ("relevant" if touched & set(c.get("requirement_ids", [])) else "regression")
            for c in criteria.get("criteria", [])}


def summarise(report: dict) -> dict:
    """Group a report's results by role: which relevant checks passed, which regressions broke."""
    out = {"relevant_failed": [], "regressions": [], "all_passed": report.get("all_passed", False)}
    for r in report.get("results", []):
        if r["status"] != "passed":
            (out["regressions"] if r.get("role") == "regression" else out["relevant_failed"]).append(r["id"])
    return out
