"""LEARNER-OWNED. Model usage and cost accounting.

Every model call is recorded with the token counts the provider reported. If a provider omits usage the
call is recorded as *unreported* (never estimated). Cost is only computed when prices are configured; otherwise
it is reported as 'unpriced'. Totals persist per project in ``runs/usage.jsonl``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class UsageLedger:
    def __init__(self, path: Path | None = None, price_in_per_m: float | None = None, price_out_per_m: float | None = None):
        self.path, self.pin, self.pout = path, price_in_per_m, price_out_per_m
        self.records: list[dict] = []

    def record(self, kind: str, model: str | None, usage: dict | None, *, latency_ms: int | None = None) -> dict:
        rec = {"ts": time.time(), "kind": kind, "model": model, "usage": usage, "latency_ms": latency_ms}
        self.records.append(rec)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    def totals(self) -> dict:
        reported = [r for r in self.records if r["usage"]]
        p = sum(r["usage"]["prompt_tokens"] for r in reported)
        c = sum(r["usage"]["completion_tokens"] for r in reported)
        out = {"model_calls": len(self.records), "calls_without_usage": len(self.records) - len(reported),
               "prompt_tokens": p, "completion_tokens": c, "models": sorted({r["model"] for r in self.records if r["model"]})}
        if self.pin is None or self.pout is None:
            out["cost"] = {"status": "unpriced", "amount": None,
                           "note": "no prices configured (APPBUILDER_PRICE_INPUT_PER_M / _OUTPUT_PER_M)"}
        else:
            amount = p / 1e6 * self.pin + c / 1e6 * self.pout
            partial = out["calls_without_usage"] > 0
            out["cost"] = {"status": "partial" if partial else "priced", "amount": round(amount, 6),
                           "note": "at configured prices; excludes calls whose usage was not reported" if partial
                           else "at configured prices"}
        return out

    @classmethod
    def load(cls, path: Path, pin=None, pout=None) -> "UsageLedger":
        led = cls(path, pin, pout)
        if path.is_file():
            led.records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return led
