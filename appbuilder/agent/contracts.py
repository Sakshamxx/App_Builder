"""LEARNER-OWNED. The data contracts the model must satisfy (output contracts).

Each contract is a Pydantic model. The model is asked for JSON matching it; we parse and validate, and
send validation errors back to the model (see ``structured.py``). Nothing here is trusted until it passes.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PAGE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}\.html$")
RID_RE = re.compile(r"^R[0-9]{1,2}$")


class Page(BaseModel):
    filename: str
    purpose: str

    @field_validator("filename")
    @classmethod
    def _fn(cls, v: str) -> str:
        if not PAGE_RE.match(v):
            raise ValueError("filename must be lowercase, like index.html or stats.html")
        return v


class Requirement(BaseModel):
    id: str
    text: str

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not RID_RE.match(v):
            raise ValueError("requirement id must look like R1, R2, ...")
        return v


class Unsupported(BaseModel):
    feature: str
    reason: str


class ScopeAssessment(BaseModel):
    """Can this request be built as a browser-only app with at most three linked pages?"""
    supported: bool
    app_title: str
    summary: str
    pages: list[Page] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    unsupported: list[Unsupported] = Field(default_factory=list)
    proposed_alternative: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.supported:
            if not 1 <= len(self.pages) <= 3:
                raise ValueError("a supported app has between 1 and 3 pages")
            names = [p.filename for p in self.pages]
            if "index.html" not in names or len(set(names)) != len(names):
                raise ValueError("pages must be unique and include index.html")
            if not 1 <= len(self.requirements) <= 10:
                raise ValueError("a supported app has between 1 and 10 requirements")
            ids = [r.id for r in self.requirements]
            if len(set(ids)) != len(ids):
                raise ValueError("requirement ids must be unique")
        elif not self.unsupported:
            raise ValueError("an unsupported request must say what is unsupported and why")
        return self


class Unverifiable(BaseModel):
    requirement_id: str
    reason: str
    proposed_alternative: str | None = None   # a supported replacement requirement the user may choose; never applied automatically


class CriteriaProposal(BaseModel):
    """Acceptance criteria in the supplied bounded vocabulary, plus requirements it cannot check."""
    fixed_time: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    criteria: list[dict]
    unverifiable: list[Unverifiable] = Field(default_factory=list)

    def document(self) -> dict:
        d: dict = {"criteria": self.criteria}
        if self.fixed_time:
            d["fixed_time"] = self.fixed_time
        if self.labels:
            d["labels"] = self.labels
        return d


class RevisionJudgment(BaseModel):
    """Does a requested change fit the approved requirements?"""
    verdict: Literal["compatible", "conflict", "unsupported"]
    explanation: str
    touched_requirement_ids: list[str] = Field(default_factory=list)
    added_requirements: list[Requirement] = Field(default_factory=list)
    conflicting_requirement_ids: list[str] = Field(default_factory=list)
    clarification_question: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.verdict == "conflict" and not (self.conflicting_requirement_ids and self.clarification_question):
            raise ValueError("a conflict must name the conflicting requirement ids and ask a clarification question")
        return self
