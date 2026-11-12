"""Per-turn latency & token telemetry.

A `TurnTrace` is bound to a ContextVar for the duration of one user turn, so any
boundary (Bhashini call, guardrail, retrieval, LLM call) can record into it with
`with stage("retrieval_ms"):` without threading the trace through every signature.
Traces only ever contain redacted text.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger("vaanisetu.telemetry")

# Canonical stage keys (spec §3 rule 4). Repeated stages (e.g. several LLM calls) accumulate.
STAGES = ("asr_ms", "nmt_ms", "guardrail_ms", "retrieval_ms", "llm_ms", "tts_ms", "total_ms")


class Span(BaseModel):
    name: str
    stage: str
    ms: float


class TurnTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    thread_id: str = ""
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    input_mode: str = "text"  # text | voice
    language: str = "en"
    intent: str | None = None
    stages: dict[str, float] = Field(default_factory=lambda: dict.fromkeys(STAGES, 0.0))
    spans: list[Span] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    pii_entities_masked: int = 0
    notes: list[str] = Field(default_factory=list)

    def add(self, stage: str, ms: float, name: str | None = None) -> None:
        self.stages[stage] = round(self.stages.get(stage, 0.0) + ms, 2)
        self.spans.append(Span(name=name or stage, stage=stage, ms=round(ms, 2)))

    def add_tokens(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.llm_calls += 1

    def note(self, message: str) -> None:
        self.notes.append(message)


_current: ContextVar[TurnTrace | None] = ContextVar("vaanisetu_trace", default=None)


def current_trace() -> TurnTrace | None:
    return _current.get()


@contextmanager
def bind_trace(trace: TurnTrace) -> Iterator[TurnTrace]:
    """Bind `trace` for the current turn and record `total_ms` on exit."""
    token = _current.set(trace)
    start = time.perf_counter()
    try:
        yield trace
    finally:
        trace.stages["total_ms"] = round((time.perf_counter() - start) * 1000, 2)
        _current.reset(token)


@contextmanager
def stage(stage_name: str, span_name: str | None = None) -> Iterator[None]:
    """Time a block and add it to the bound trace (no-op if no trace is bound)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        trace = _current.get()
        if trace is not None:
            trace.add(stage_name, (time.perf_counter() - start) * 1000, span_name)


def record_tokens(prompt: int, completion: int) -> None:
    trace = _current.get()
    if trace is not None:
        trace.add_tokens(prompt, completion)


def persist(trace: TurnTrace, path: Path) -> None:
    """Append a trace as one JSON line. Failures are logged, never raised."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(trace.model_dump(), ensure_ascii=False) + "\n")
    except OSError as exc:  # telemetry must never break a user turn
        logger.warning("Could not persist trace %s: %s", trace.trace_id, exc)
