"""OpenAI calls behind a small protocol, so the agent does not depend on one LLM client (ADR-4).

Every call is timed into `llm_ms` (span `llm.<task>`) and its token usage recorded.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from config.settings import Settings
from src.agent import prompts
from src.agent.state import GrievanceCategory, Intent, Occupation, SchemeName, Urgency
from src.telemetry import record_tokens, stage


# --- Structured outputs (all fields required-but-nullable for OpenAI strict mode) -------
class RouterDecision(BaseModel):
    intent: Intent
    scheme: Literal["PM_KISAN", "PM_JAY", "CPGRAMS"] | None
    search_query: str
    user_language: str = Field(description='"en", "hinglish" or an ISO 639-1 code')


INCOME_UNITS = {"rupees": 1, "thousand": 1_000, "lakh": 100_000, "crore": 10_000_000}


class IncomeStatement(BaseModel):
    amount: float = Field(description="the number exactly as stated, e.g. 18 for '18 lakh', 15 for '15 hazaar'")
    unit: Literal["rupees", "thousand", "lakh", "crore"]
    period: Literal["month", "year"]

    def annual_inr(self) -> int:
        return round(self.amount * INCOME_UNITS[self.unit] * (12 if self.period == "month" else 1))


class EligibilityUpdate(BaseModel):
    scheme_name: SchemeName | None
    state: str | None
    occupation: Occupation | None
    land_holding_hectares: float | None
    income: IncomeStatement | None  # as stated; code converts to annual rupees (no LLM arithmetic)
    is_income_tax_payer: bool | None
    has_aadhaar_linked_bank: bool | None
    age_years: int | None
    occupation_description: str | None = Field(description="the citizen's own words about their work, in English")


class OccupationDecision(BaseModel):
    # Fields are generated in order: translate -> compare with named occupations -> decide.
    job_in_english: str = Field(description="translate the citizen's exact words about their work into English")
    named_occupations_considered: list[str] = Field(
        description="occupations named in the OFFICIAL RULES that are closest to this job"
    )
    best_match: str = Field(
        description="the single closest named occupation, or 'none' if no named occupation is a reasonable fit"
    )
    occupation: Occupation


class GrievanceUpdate(BaseModel):
    ministry_or_department: str | None
    state: str | None
    district: str | None
    scheme_reference: str | None
    incident_summary: str | None
    grievance_category: GrievanceCategory | None
    urgency: Urgency | None


class GroundedAnswer(BaseModel):
    answer: str
    cited_chunk_ids: list[str]
    answerable: bool


class ConfirmationDecision(BaseModel):
    decision: Literal["CONFIRM", "CANCEL", "EDIT"]


SlotUpdate = EligibilityUpdate | GrievanceUpdate


class AgentLLM(Protocol):
    async def route(
        self, history: str, message: str, active_flow: str | None, pending_question: str | None
    ) -> RouterDecision: ...

    async def extract_slots(self, flow: Intent, history: str, message: str, current: dict[str, Any]) -> SlotUpdate: ...

    async def answer(self, question: str, passages: list[dict[str, Any]]) -> GroundedAnswer: ...

    async def classify_confirmation(self, reply: str, prompt: str) -> ConfirmationDecision: ...

    async def classify_occupation(self, description: str, original: str) -> Occupation: ...

    async def summarize(self, transcript: str) -> str: ...

    async def translate(self, text: str, target_language: str) -> str: ...


class LLMError(RuntimeError):
    pass


class OpenAIAgentLLM:
    def __init__(self, settings: Settings, policy_rules: dict[str, str] | None = None) -> None:
        if not settings.openai_enabled:
            raise RuntimeError("OPENAI_API_KEY is not set. Add it to .env (see .env.example).")
        if policy_rules is None:
            from src.rag.ingest import load_corpus

            policy_rules = prompts.policy_rules(load_corpus(settings.policies_dir))
        # Classification prompts carry the official rule text, so they follow the documents.
        self._eligibility_system = prompts.ELIGIBILITY_EXTRACT_SYSTEM.replace(
            prompts.POLICY_MARKER, policy_rules["occupation"]
        )
        self._occupation_system = prompts.OCCUPATION_CLASSIFY_SYSTEM.replace(
            prompts.POLICY_MARKER, policy_rules["occupation"]
        )
        self._grievance_system = prompts.GRIEVANCE_EXTRACT_SYSTEM.replace(
            prompts.POLICY_MARKER, policy_rules["grievance"]
        )
        common: dict[str, Any] = dict(
            api_key=settings.openai_api_key,
            temperature=0,
            timeout=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
        )
        self.main = ChatOpenAI(model=settings.openai_model, **common)
        self.small = ChatOpenAI(model=settings.openai_small_model, **common)

    async def _structured(self, llm: ChatOpenAI, schema: type[BaseModel], msgs, task: str):
        runnable = llm.with_structured_output(schema, method="json_schema", strict=True, include_raw=True)
        with stage("llm_ms", f"llm.{task}"):
            out = await runnable.ainvoke(msgs)
        _record_usage(out["raw"])
        if out.get("parsing_error") or out.get("parsed") is None:
            raise LLMError(f"{task}: could not parse structured output: {out.get('parsing_error')}")
        return out["parsed"]

    async def _text(self, llm: ChatOpenAI, msgs: list[BaseMessage], task: str, stage_name: str = "llm_ms") -> str:
        with stage(stage_name, f"llm.{task}"):
            msg = await llm.ainvoke(msgs)
        _record_usage(msg)
        return str(msg.content).strip()

    async def route(self, history, message, active_flow, pending_question) -> RouterDecision:
        if active_flow:
            ctx = f"active flow = {active_flow}; last question asked = {pending_question!r}"
        else:
            ctx = "no active flow"
        system = prompts.ROUTER_SYSTEM.replace("{flow_context}", ctx)
        human = f"Recent conversation:\n{history or '(none)'}\n\nLatest user message:\n{message}"
        return await self._structured(self.main, RouterDecision, [SystemMessage(system), HumanMessage(human)], "router")

    async def extract_slots(self, flow, history, message, current) -> SlotUpdate:
        if flow == Intent.ELIGIBILITY_CHECK:
            system, schema = self._eligibility_system, EligibilityUpdate
        else:
            system, schema = self._grievance_system, GrievanceUpdate
        human = (
            f"Details collected so far: {current or '{}'}\n\n"
            f"Recent conversation:\n{history or '(none)'}\n\nLatest user message:\n{message}"
        )
        return await self._structured(self.main, schema, [SystemMessage(system), HumanMessage(human)], "slot_extract")

    async def answer(self, question, passages) -> GroundedAnswer:
        context = "\n\n".join(f"[{p['chunk_id']}] {p['label']}\n{p['body']}" for p in passages)
        human = f"Policy passages:\n{context}\n\nCitizen's question: {question}"
        return await self._structured(
            self.main,
            GroundedAnswer,
            [SystemMessage(prompts.ANSWER_SYSTEM), HumanMessage(human)],
            "answer",
        )

    async def classify_occupation(self, description, original) -> Occupation:
        """Fallback when extraction couldn't place the work in a category: a focused second pass."""
        decision = await self._structured(
            self.main,
            OccupationDecision,
            [
                SystemMessage(self._occupation_system),
                HumanMessage(f"Citizen's exact words: {original}\n\nSummary of their work: {description}"),
            ],
            "occupation_classify",
        )
        return decision.occupation

    async def classify_confirmation(self, reply, prompt) -> ConfirmationDecision:
        human = f"Confirmation prompt shown:\n{prompt}\n\nCitizen's reply:\n{reply}"
        return await self._structured(
            self.small,
            ConfirmationDecision,
            [SystemMessage(prompts.CONFIRMATION_SYSTEM), HumanMessage(human)],
            "confirmation",
        )

    async def summarize(self, transcript) -> str:
        msgs = [SystemMessage(prompts.SUMMARY_SYSTEM), HumanMessage(transcript)]
        return await self._text(self.small, msgs, "summary")

    async def translate(self, text, target_language) -> str:
        target = prompts.LANGUAGE_NAMES.get(target_language, target_language)
        msgs = [
            SystemMessage(prompts.TRANSLATE_SYSTEM),
            HumanMessage(f"Target language: {target}\n\nMessage:\n{text}"),
        ]
        return await self._text(self.small, msgs, "translate", stage_name="nmt_ms")


def _record_usage(message: Any) -> None:
    usage = getattr(message, "usage_metadata", None) or {}
    record_tokens(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
