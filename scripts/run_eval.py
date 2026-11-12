"""Reproducible benchmark for VaaniSetu AI (CLAUDE.md ADR-5).

Runs every case in data/eval_dataset.json through the real pipeline (fresh thread per case)
and reports:
  * Faithfulness      — RAGAS definition: supported claims / total claims (LLM judge, temp 0)
  * Answer relevancy  — LLM-judged 1-5 relevance to the question, scaled to 0-1
  * Intent accuracy, retrieval hit-rate@k, citation accuracy, refusal accuracy
  * Slot-extraction accuracy (field level), eligibility-verdict accuracy, grievance completion
  * Latency p50 / p95 per turn, LLM calls and tokens per turn

Usage:  python scripts/run_eval.py [--limit N] [--only qa-01,elig-02] [--update-readme]
Note: stop the web app (python -m server) first — Qdrant local mode allows one process per data directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from config.settings import get_settings  # noqa: E402
from src.agent.nodes import NO_ANSWER_REPLY  # noqa: E402

DATASET = ROOT / "data" / "eval_dataset.json"
OUT_DIR = ROOT / "eval_results"
README = ROOT / "README.md"
MARK_START, MARK_END = "<!-- BENCHMARK:START -->", "<!-- BENCHMARK:END -->"


# --- Scoring helpers ----------
def value_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list):
        return any(value_matches(e, actual) for e in expected)
    if isinstance(expected, dict) and "contains" in expected:
        return isinstance(actual, str) and expected["contains"].lower() in actual.lower()
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if expected == 0:
            return actual == 0
        return math.isclose(expected, actual, rel_tol=0.10)
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().lower() == actual.strip().lower()
    return expected == actual


def score_slots(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[int, int, list[str]]:
    misses = [f for f, e in expected.items() if not value_matches(e, actual.get(f))]
    return len(expected) - len(misses), len(expected), misses


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def as_list(x: Any) -> list[Any]:
    return x if isinstance(x, list) else [x]


# --- LLM judges ----------
class Claim(BaseModel):
    claim: str
    supported: bool


class FaithfulnessJudgement(BaseModel):
    claims: list[Claim]


class RelevancyJudgement(BaseModel):
    score: int = Field(description="1 (irrelevant/evasive) to 5 (directly and fully answers)")
    reason: str


FAITHFULNESS_SYSTEM = """You audit answers from a government-scheme assistant. Break the ANSWER \
into atomic factual claims (ignore citation markers, greetings and any follow-up question). For \
each claim decide whether it is directly supported by the CONTEXT passages. A claim that adds \
facts not stated in the context is unsupported."""

RELEVANCY_SYSTEM = """Rate from 1 to 5 how directly and completely the ANSWER addresses the \
citizen's QUESTION (5 = fully answers it, 3 = partially, 1 = irrelevant or evasive). Judge \
relevance only, not correctness."""


class Judge:
    def __init__(self) -> None:
        s = get_settings()
        llm = ChatOpenAI(model=s.openai_model, temperature=0, api_key=s.openai_api_key)
        self._faith = llm.with_structured_output(FaithfulnessJudgement, method="json_schema")
        self._rel = llm.with_structured_output(RelevancyJudgement, method="json_schema")

    async def faithfulness(self, answer: str, contexts: list[str]) -> float | None:
        context = "\n\n".join(contexts)
        out = await self._faith.ainvoke(
            [SystemMessage(FAITHFULNESS_SYSTEM),
             HumanMessage(f"CONTEXT:\n{context}\n\nANSWER:\n{answer}")]
        )  # fmt: skip
        if not out.claims:
            return None
        return sum(c.supported for c in out.claims) / len(out.claims)

    async def relevancy(self, question: str, answer: str) -> float:
        out = await self._rel.ainvoke(
            [SystemMessage(RELEVANCY_SYSTEM),
             HumanMessage(f"QUESTION:\n{question}\n\nANSWER:\n{answer}")]
        )  # fmt: skip
        return (min(max(out.score, 1), 5) - 1) / 4


# --- Runner ----------
async def run_case(pipeline, judge: Judge, case: dict[str, Any]) -> dict[str, Any]:
    thread = f"eval-{case['id']}-{uuid.uuid4().hex[:6]}"
    results = []
    slots: dict[str, Any] = {}
    for text in case["turns"]:
        r = await pipeline.handle_turn(thread, text=text, language="auto")
        results.append(r)
        if r.slots:
            slots = r.slots
    payload = results[-1].final_payload
    if case.get("auto_confirm") and results[-1].awaiting_confirmation:
        r = await pipeline.handle_turn(thread, text="yes", language="en")
        results.append(r)
        payload = r.final_payload

    first = results[0]
    row: dict[str, Any] = {
        "id": case["id"],
        "category": case["category"],
        "intent": first.intent,
        "intent_ok": first.intent in as_list(case["expected_intent"]),
        "turn_latency_ms": [x.trace.stages["total_ms"] for x in results],
        "llm_calls": [x.trace.llm_calls for x in results],
        "tokens": [x.trace.prompt_tokens + x.trace.completion_tokens for x in results],
        "response_en": first.response_en,  # redacted
    }

    if "expected_citations_any" in case:
        expected = set(case["expected_citations_any"])
        retrieved = {p["chunk_id"] for p in first.retrieved}
        row["retrieval_hit"] = bool(expected & retrieved)
        row["citation_ok"] = bool(expected & set(first.citations))
        if first.citations:
            contexts = [p["body"] for p in first.retrieved]
            row["faithfulness"] = await judge.faithfulness(first.response_en, contexts)
            row["relevancy"] = await judge.relevancy(case["turns"][0], first.response_en)

    if case.get("expect_refusal"):
        row["refusal_ok"] = first.intent == "OUT_OF_SCOPE" or first.response_en == NO_ANSWER_REPLY

    if "expected_slots" in case:
        correct, total, misses = score_slots(case["expected_slots"], slots)
        row |= {"slots_correct": correct, "slots_total": total, "slot_misses": misses,
                "slots": slots, "payload_emitted": payload is not None}  # fmt: skip

    if "expected_verdict" in case:
        verdict = (payload or {}).get("eligibility_verdict") or {}
        row["verdict"] = verdict.get("verdict")
        row["verdict_ok"] = verdict.get("verdict") == case["expected_verdict"]
    return row


def mean(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return statistics.fmean(float(v) for v in vals) if vals else None


def aggregate(rows: list[dict]) -> dict[str, Any]:
    lat = [ms for r in rows for ms in r["turn_latency_ms"]]
    slot_rows = [r for r in rows if "slots_total" in r]
    grievances = [r for r in rows if r["category"].startswith("grievance")]
    return {
        "cases": len(rows),
        "turns": len(lat),
        "intent_accuracy": mean(rows, "intent_ok"),
        "retrieval_hit_rate": mean(rows, "retrieval_hit"),
        "citation_accuracy": mean(rows, "citation_ok"),
        "faithfulness": mean(rows, "faithfulness"),
        "answer_relevancy": mean(rows, "relevancy"),
        "refusal_accuracy": mean(rows, "refusal_ok"),
        "slot_extraction_accuracy": (
            sum(r["slots_correct"] for r in slot_rows) / sum(r["slots_total"] for r in slot_rows)
            if slot_rows else None
        ),
        "verdict_accuracy": mean(rows, "verdict_ok"),
        "grievance_completion": (
            statistics.fmean(r["payload_emitted"] for r in grievances) if grievances else None
        ),
        "latency_p50_ms": percentile(lat, 0.50),
        "latency_p95_ms": percentile(lat, 0.95),
        "llm_calls_per_turn": statistics.fmean(c for r in rows for c in r["llm_calls"]),
        "tokens_per_turn": statistics.fmean(t for r in rows for t in r["tokens"]),
    }  # fmt: skip


LABELS = {
    "faithfulness": "Faithfulness (supported claims / claims)",
    "answer_relevancy": "Answer relevancy (LLM judge, 0–1)",
    "citation_accuracy": "Citation accuracy (expected section cited)",
    "retrieval_hit_rate": "Retrieval hit-rate@k (hybrid RRF)",
    "intent_accuracy": "Intent routing accuracy",
    "slot_extraction_accuracy": "Slot-extraction accuracy (field level)",
    "verdict_accuracy": "Eligibility verdict accuracy",
    "grievance_completion": "Grievance payload completion",
    "refusal_accuracy": "Refusal accuracy (unanswerable)",
}


def to_markdown(agg: dict[str, Any], rows: list[dict], model: str) -> str:
    def fmt(v):
        return "n/a" if v is None else f"{v:.2f}"

    lines = [
        f"_Run {time.strftime('%Y-%m-%d')} · model `{model}` · {agg['cases']} cases / "
        f"{agg['turns']} turns · `python scripts/run_eval.py`_",
        "",
        "| Metric | Score |",
        "|---|---|",
    ]
    lines += [f"| {label} | {fmt(agg[key])} |" for key, label in LABELS.items()]
    lines += [
        f"| Latency p50 / p95 per turn | {agg['latency_p50_ms']:.0f} ms / {agg['latency_p95_ms']:.0f} ms |",
        f"| LLM calls / tokens per turn | {agg['llm_calls_per_turn']:.1f} / {agg['tokens_per_turn']:.0f} |",
    ]
    failures = [r for r in rows if not all(r.get(k, True) for k in
                ("intent_ok", "citation_ok", "verdict_ok", "refusal_ok"))
                or r.get("slot_misses")]  # fmt: skip
    if failures:
        lines += ["", "<details><summary>Failed checks</summary>", ""]
        for r in failures:
            bad = [k for k in ("intent_ok", "citation_ok", "verdict_ok", "refusal_ok")
                   if r.get(k) is False]  # fmt: skip
            misses = f" slot misses: {r['slot_misses']}" if r.get("slot_misses") else ""
            lines.append(f"- `{r['id']}`: {', '.join(bad)}{misses}")
        lines += ["", "</details>"]
    return "\n".join(lines)


def update_readme(markdown: str) -> None:
    text = README.read_text(encoding="utf-8")
    if MARK_START not in text:
        print("README has no benchmark markers; skipped.")
        return
    head, rest = text.split(MARK_START, 1)
    _, tail = rest.split(MARK_END, 1)
    README.write_text(f"{head}{MARK_START}\n{markdown}\n{MARK_END}{tail}", encoding="utf-8")
    print("README.md benchmark table updated.")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--update-readme", action="store_true")
    args = parser.parse_args()

    from src.pipeline import VaaniSetuPipeline

    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    cases = cases[: args.limit] if args.limit else cases

    settings = get_settings()
    pipeline = await VaaniSetuPipeline.create(settings)
    pipeline.persist_traces = False
    judge = Judge()
    rows = []
    try:
        for i, case in enumerate(cases, 1):
            row = await run_case(pipeline, judge, case)
            rows.append(row)
            checks = {k: row[k] for k in ("intent_ok", "citation_ok", "verdict_ok", "refusal_ok")
                      if k in row}  # fmt: skip
            print(
                f"[{i:02d}/{len(cases)}] {case['id']:<9} {checks}"
                + (f" slot misses={row['slot_misses']}" if row.get("slot_misses") else "")
            )
    finally:
        await pipeline.aclose()

    agg = aggregate(rows)
    markdown = to_markdown(agg, rows, settings.openai_model)
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "results.json").write_text(
        json.dumps({"aggregate": agg, "cases": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (OUT_DIR / "benchmark.md").write_text(markdown + "\n", encoding="utf-8")
    print("\n" + markdown)
    if args.update_readme:
        update_readme(markdown)


if __name__ == "__main__":
    asyncio.run(main())
