"""Settings and limits for the reference application.

Secrets come from the environment only. They are never written to disk, to exports, to browser
storage, or to logs (see ``supplied/redact.py`` and the tests in ``tests/test_boundaries.py``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Limits:
    """Every numeric limit in one place so that tests and lessons can point at it."""

    # request / scope
    max_request_chars: int = 4000
    max_pages: int = 3
    # workspace (the generated app's files)
    max_files: int = 8
    max_file_bytes: int = 64_000
    max_total_bytes: int = 256_000
    # agent loop
    max_model_turns: int = 20          # step cap: model calls in one build
    max_tool_calls_per_turn: int = 6   # extra calls in one turn are refused as data
    max_repairs: int = 3               # repair attempts after a failed trusted check
    max_diagnostics: int = 5           # agent-proposed diagnostic runs per build (never counted as acceptance)
    max_requirements: int = 10
    backoff_base_s: float = 1.0        # transient retry backoff: base * 2**attempt, capped
    backoff_cap_s: float = 20.0
    transient_retry_attempts: int = 3  # transport retries (separate from repairs)
    structured_retry_attempts: int = 2 # re-asks after an invalid structured output
    # criteria vocabulary
    max_criteria: int = 12
    max_steps_per_criterion: int = 20
    max_step_value_chars: int = 200
    # harness
    check_timeout_s: float = 90.0
    step_timeout_ms: int = 3000
    # import
    max_import_bytes: int = 2_000_000
    max_import_files: int = 40


@dataclass
class Settings:
    data_dir: Path
    limits: Limits = field(default_factory=Limits)
    provider: str = "stub"             # gemini | ollama | stub
    model: str | None = None           # explicit: model ids change, so there is no guessed default
    implementation: str = "handwritten"  # handwritten | framework
    api_key: str | None = field(default=None, repr=False)
    app_password: str | None = field(default=None, repr=False)   # APP_PASSWORD: HTTP Basic gate for a hosted demo; unset = open
    base_url: str | None = None
    api_port: int = 8080
    preview_port: int = 8081
    structured_mode: str = "prompt_only"  # prompt_only | json_object | json_schema
    price_input_per_m: float | None = None   # optional; cost is "unpriced" when absent
    price_output_per_m: float | None = None

    GEMINI_COMPAT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
    OLLAMA_BASE = "http://localhost:11434/v1/"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env

        def f(name: str) -> float | None:
            v = e.get(name)
            return float(v) if v else None

        provider = e.get("APPBUILDER_PROVIDER", "stub")
        base = e.get("APPBUILDER_BASE_URL") or (
            cls.GEMINI_COMPAT_BASE if provider == "gemini" else cls.OLLAMA_BASE if provider == "ollama" else None
        )
        return cls(
            data_dir=Path(e.get("APPBUILDER_DATA_DIR", "./data")).resolve(),
            provider=provider,
            model=e.get("APPBUILDER_MODEL"),
            implementation=e.get("APPBUILDER_IMPL", "handwritten"),
            api_key=e.get("GEMINI_API_KEY") if provider == "gemini" else e.get("OLLAMA_API_KEY", "ollama"),
            base_url=base,
            app_password=e.get("APP_PASSWORD") or None,
            api_port=int(e.get("APPBUILDER_API_PORT", "8080")),
            preview_port=int(e.get("APPBUILDER_PREVIEW_PORT", "8081")),
            structured_mode=e.get("APPBUILDER_STRUCTURED_MODE", "prompt_only"),
            price_input_per_m=f("APPBUILDER_PRICE_INPUT_PER_M"),
            price_output_per_m=f("APPBUILDER_PRICE_OUTPUT_PER_M"),
        )

    def secrets(self) -> list[str]:
        """Values that must never appear in logs, traces, exports or responses."""
        return [s for s in [self.api_key, self.app_password] if s and len(s) >= 8]

    def live_model_configured(self) -> bool:
        return self.provider in ("gemini", "ollama") and bool(self.model) and bool(self.api_key)
