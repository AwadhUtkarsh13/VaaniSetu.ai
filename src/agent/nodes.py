"""LangGraph nodes: router, hybrid RAG, grounded answer, slot filler, HITL gate, payload."""

from __future__ import annotations

import re
from typing import Any, Literal

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command, interrupt

from src.agent.eligibility import evaluate
from src.agent.llm import AgentLLM
from src.agent.state import (
    FLOW_INTENTS,
    SLOT_MODELS,
    AgentState,
    EligibilitySlots,
    EligibilityVerdict,
    FinalActionPayload,
    GrievanceSlots,
    Intent,
    Occupation,
    merge_slots,
    missing_slots,
)
from src.rag.retriever import HybridRetriever
from src.telemetry import current_trace

# Product copy (English; localised downstream). One question per missing slot.
FIELD_QUESTIONS: dict[str, str] = {
    "scheme_name": "Which scheme should I check for you — PM-KISAN (₹6,000 a year income "
    "support for landholding farmers) or Ayushman Bharat PM-JAY (₹5 lakh health cover)?",
    "state": "Which State or Union Territory do you live in?",
    "occupation": "What is your main occupation? (for example farmer, daily-wage labourer, "
    "government employee, shopkeeper, domestic worker)",
    "land_holding_hectares": "How much cultivable land is recorded in your family's name? "
    "You can answer in acres or hectares — say 0 if none.",
    "annual_income_inr": "Roughly what is your household's total income? Monthly or yearly is fine.",
    "is_income_tax_payer": "Did you or any member of your family pay income tax in the last assessment year?",
    "has_aadhaar_linked_bank": "Is your bank account linked (seeded) with your Aadhaar?",
    "age_years": "What is the age of the person who needs health cover?",
    "ministry_or_department": "Which ministry, department or government office is your complaint about?",
    "district": "Which district did this happen in?",
    "incident_summary": "Please describe what happened — what, when and where, and any "
    "application or registration number you have.",
    "grievance_category": "What kind of problem is this — a payment delay, wrongful rejection, "
    "bribe demand, staff misconduct, denial of service or treatment, or a record error?",
    "urgency": "Is there an immediate risk to anyone's health, safety or livelihood?",
}

FIELD_LABELS: dict[str, str] = {
    "scheme_name": "Scheme", "state": "State / UT", "occupation": "Occupation",
    "land_holding_hectares": "Land holding (ha)", "annual_income_inr": "Annual income (₹)",
    "is_income_tax_payer": "Income-tax payer", "has_aadhaar_linked_bank": "Aadhaar-linked bank",
    "age_years": "Age", "ministry_or_department": "Ministry / Department", "district": "District",
    "scheme_reference": "Scheme / reference no.", "incident_summary": "What happened",
    "grievance_category": "Category", "urgency": "Urgency",
}  # fmt: skip

OUT_OF_SCOPE_REPLY = (
    "I can help with three things: questions about PM-KISAN and Ayushman Bharat PM-JAY, "
    "checking whether you are eligible for them, and preparing a public grievance for CPGRAMS. "
    "How can I help you with one of these?"
)
NO_ANSWER_REPLY = (
    "I couldn't find this in the official scheme documents available to me, so I don't want to "
    "guess. For PM-KISAN you can call 155261, for PM-JAY 14555."
)
CANCELLED_REPLY = "Okay, I have cancelled this and nothing was submitted. How else can I help?"

GRIEVANCE_CITATIONS = ["cpgrams:2.1", "cpgrams:2.2", "cpgrams:4.1", "cpgrams:5.1", "cpgrams:6.1"]
# One bracket holding one or more chunk ids: "[pm_kisan:2.1]" or "[pm_kisan:2.1, pm_jay:3.5]".
_CITE = re.compile(r"\[(?:[a-z_]+:\d+(?:\.\d+)?(?:#\d+)?(?:\s*[,;]\s*)?)+\]")
HISTORY_TURNS = 8


def _flow_key(flow: str) -> str:
    return "eligibility_slots" if flow == Intent.ELIGIBILITY_CHECK else "grievance_slots"


def history_text(messages: list[AnyMessage], current: str | None = None) -> str:
    msgs = list(messages)
    if msgs and isinstance(msgs[-1], HumanMessage) and msgs[-1].content == current:
        msgs = msgs[:-1]
    lines = []
    for m in msgs[-HISTORY_TURNS:]:
        role = "Citizen" if isinstance(m, HumanMessage) else "Assistant"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


SCHEME_DISPLAY = {"PM_KISAN": "PM-KISAN", "PM_JAY": "Ayushman Bharat PM-JAY"}


def format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str) and value in SCHEME_DISPLAY:
        return SCHEME_DISPLAY[value]
    if isinstance(value, str):
        # Prettify enum values ("informal_urban_worker") but never touch PII tokens (<NAME_1>).
        return value.replace("_", " ") if re.fullmatch(r"[a-z_]+|[A-Z_]+", value) else value
    return f"{value:,}" if isinstance(value, int) else str(value)


def format_summary(slots: dict[str, Any]) -> str:
    return "\n".join(f"- {FIELD_LABELS.get(k, k)}: {format_value(v)}" for k, v in slots.items() if v is not None)


def format_verdict(v: EligibilityVerdict) -> str:
    scheme = {"PM_KISAN": "PM-KISAN", "PM_JAY": "Ayushman Bharat PM-JAY"}[v.scheme_name]
    headline = {
        "ELIGIBLE": f"**Good news — you appear to be eligible for {scheme}.**",
        "LIKELY_ELIGIBLE": f"**You are likely eligible for {scheme}**, but this still needs to be "
        "confirmed in the official beneficiary list.",
        "NEEDS_VERIFICATION": f"**I can't confirm {scheme} for you yet** — it needs to be checked "
        "in the official records.",
        "NOT_ELIGIBLE": f"**Based on the official rules, you are not eligible for {scheme}.**",
    }[v.verdict]
    parts = [headline, "", "**Why:**"]
    parts += [f"- {r}" for r in v.reasons]
    if v.next_steps:
        parts += ["", "**What to do next:**"] + [f"- {s}" for s in v.next_steps]
    return "\n".join(parts)


def render_citations(text: str, labels: dict[str, str]) -> str:
    """Turn `[pm_kisan:4.6]` markers into readable `[PM-KISAN §4.6]` labels."""

    def short(cid: str) -> str:
        doc, _, sec = cid.partition(":")
        return f"{labels.get(cid, doc.upper().replace('_', '-'))} §{sec}"

    def repl(m: re.Match[str]) -> str:
        ids = re.findall(r"[a-z_]+:\d+(?:\.\d+)?", m.group(0))
        return "[" + "; ".join(short(i) for i in ids) + "]"

    return _CITE.sub(repl, text)


class AgentNodes:
    def __init__(self, llm: AgentLLM, retriever: HybridRetriever, top_k: int = 8) -> None:
        self.llm = llm
        self.retriever = retriever
        self.top_k = top_k

    # --- Router ---------------------------------------------------------------------
    async def intent_router(self, state: AgentState) -> dict[str, Any]:
        message = state["user_message"]
        decision = await self.llm.route(
            history_text(state.get("messages", []), message),
            message,
            state.get("active_flow"),
            state.get("pending_question"),
        )
        update: dict[str, Any] = {
            "intent": decision.intent.value,
            "scheme_filter": decision.scheme,
            "search_query": decision.search_query,
            "user_language": decision.user_language,
            "retrieved": [],
            "citations": [],
            "final_payload": None,
            "response": "",
        }
        if decision.intent in FLOW_INTENTS:
            update["active_flow"] = decision.intent.value
        return update

    @staticmethod
    def route_after_router(state: AgentState) -> str:
        intent = state["intent"]
        if intent == Intent.POLICY_QA:
            return "hybrid_rag_retriever"
        if intent in FLOW_INTENTS:
            return "slot_filler"
        return "out_of_scope"

    # --- Policy Q&A -------------------------------------------------------------------
    async def hybrid_rag_retriever(self, state: AgentState) -> dict[str, Any]:
        query = state.get("search_query") or state["user_message"]
        return {"retrieved": await self._search(query, self.top_k, state.get("scheme_filter"))}

    async def _search(self, query: str, top_k: int, scheme: str | None) -> list[dict[str, Any]]:
        results = await self.retriever.retrieve(query, top_k, scheme=scheme)
        if not results and scheme:  # filter too narrow -> search everything
            results = await self.retriever.retrieve(query, top_k)
        return [
            {
                "chunk_id": r.chunk.chunk_id,
                "label": r.chunk.label,
                "body": r.chunk.body,
                "score": r.score,
                "bm25_rank": r.bm25_rank,
                "dense_rank": r.dense_rank,
            }
            for r in results
        ]

    async def _answer(self, question: str, passages: list[dict[str, Any]]) -> tuple[Any, list[str]]:
        """Ask the answerer; return (result, valid citations). No valid citation = not answered."""
        if not passages:
            return None, []
        result = await self.llm.answer(question, passages)
        known = {p["chunk_id"] for p in passages}
        cited = [c for c in result.cited_chunk_ids if c in known]
        cited += [c for c in re.findall(r"[a-z_]+:\d+(?:\.\d+)?", result.answer) if c in known and c not in cited]
        return result, (cited if result.answerable else [])

    async def grounded_answer(self, state: AgentState) -> dict[str, Any]:
        passages = list(state.get("retrieved", []))
        question = state["user_message"]
        if state.get("search_query"):
            question = f"{state['search_query']}\n(Original wording: {state['user_message']})"
        result, cited = await self._answer(question, passages)

        out: dict[str, Any] = {}
        if not cited and len(passages) >= self.top_k:
            # Corrective retrieval: the answer may sit just below the cut-off (e.g. strong in dense
            # search, weak in BM25 because of different wording). Widen the search once and retry.
            query = state.get("search_query") or state["user_message"]
            wider = await self._search(query, self.top_k * 2, state.get("scheme_filter"))
            if len(wider) > len(passages):
                trace = current_trace()
                if trace is not None:
                    trace.note("retrieval_widened")
                passages = out["retrieved"] = wider
                result, cited = await self._answer(question, passages)

        if not cited:
            text = NO_ANSWER_REPLY
        else:
            labels = {p["chunk_id"]: p["label"].split(" §")[0] for p in passages}
            text = render_citations(result.answer, labels)
        text = self._with_flow_reminder(state, text)
        return {**out, "response": text, "citations": cited, "messages": [AIMessage(text)]}

    async def out_of_scope(self, state: AgentState) -> dict[str, Any]:
        text = self._with_flow_reminder(state, OUT_OF_SCOPE_REPLY)
        return {"response": text, "messages": [AIMessage(text)]}

    @staticmethod
    def _with_flow_reminder(state: AgentState, text: str) -> str:
        if state.get("active_flow") and state.get("pending_question"):
            return f"{text}\n\nTo continue where we left off: {state['pending_question']}"
        return text

    # --- Slot filling -------------------------------------------------------------------------
    async def slot_filler(self, state: AgentState) -> dict[str, Any]:
        flow = Intent(state["active_flow"])
        key = _flow_key(flow)
        current = dict(state.get(key) or {})
        message = state["user_message"]
        update = await self.llm.extract_slots(flow, history_text(state.get("messages", []), message), message, current)
        values = update.model_dump()
        income = getattr(update, "income", None)
        if income is not None:  # the LLM reports what was said; arithmetic happens here
            values["annual_income_inr"] = income.annual_inr()
        values.pop("income", None)
        # Work the extractor couldn't place in a category gets a focused, policy-grounded second pass.
        description = values.pop("occupation_description", None)
        if description and values.get("occupation") in (None, Occupation.OTHER):
            values["occupation"] = await self.llm.classify_occupation(description, message)
        merged, errors = merge_slots(SLOT_MODELS[flow], current, values)
        missing = missing_slots(flow, merged)

        if errors:
            field, reason = next(iter(errors.items()))
            question = (
                f"Sorry, I couldn't accept the {FIELD_LABELS.get(field, field).lower()} "
                f"({reason}). {FIELD_QUESTIONS.get(field, '')}"
            ).strip()
        elif missing:
            question = FIELD_QUESTIONS[missing[0]]
        else:
            question = None

        out: dict[str, Any] = {key: merged, "slot_errors": errors, "pending_question": question}
        if question:
            out["response"] = question
            out["messages"] = [AIMessage(question)]
        return out

    @staticmethod
    def route_after_slots(state: AgentState) -> str:
        return END if state.get("pending_question") else "human_confirmation_gate"

    # --- Human-in-the-loop confirmation ----------------------------------------------------------
    async def human_confirmation_gate(
        self, state: AgentState
    ) -> Command[Literal["payload_generator", "slot_filler", "__end__"]]:
        flow = state["active_flow"]
        slots = state.get(_flow_key(flow)) or {}
        action = "check your eligibility" if flow == Intent.ELIGIBILITY_CHECK else "prepare your grievance for CPGRAMS"
        prompt = (
            f"Before I {action}, please confirm these details:\n{format_summary(slots)}\n\n"
            "Reply 'yes' to confirm, 'no' to cancel, or tell me what to change."
        )
        # Graph pauses here; the pipeline resumes it with the (redacted) next user turn.
        reply = interrupt({"type": "confirmation", "prompt": prompt, "flow": flow})

        decision = (await self.llm.classify_confirmation(reply, prompt)).decision
        transcript = [AIMessage(prompt), HumanMessage(reply)]
        if decision == "CONFIRM":
            return Command(goto="payload_generator", update={"messages": transcript})
        if decision == "CANCEL":
            return Command(
                goto=END,
                update={
                    "messages": [*transcript, AIMessage(CANCELLED_REPLY)],
                    "response": CANCELLED_REPLY,
                    "active_flow": None,
                    "pending_question": None,
                    _flow_key(flow): {},
                },
            )
        return Command(goto="slot_filler", update={"messages": transcript, "user_message": reply})

    # --- Payload --------------------------------------------------------------------------------
    async def payload_generator(self, state: AgentState) -> dict[str, Any]:
        flow = state["active_flow"]
        key = _flow_key(flow)
        slots = state.get(key) or {}
        summary = await self.llm.summarize(history_text(state.get("messages", [])))

        if flow == Intent.ELIGIBILITY_CHECK:
            validated = EligibilitySlots(**slots)
            verdict = evaluate(validated)
            payload = FinalActionPayload(
                action_type="SCHEME_ELIGIBILITY_CHECK",
                validated_slots=validated.model_dump(mode="json", exclude_none=True),
                eligibility_verdict=verdict,
                citations=verdict.citations,
                redacted_transcript_summary=summary,
            )
            text = format_verdict(verdict)
        else:
            validated_g = GrievanceSlots(**slots)
            payload = FinalActionPayload(
                action_type="CPGRAMS_GRIEVANCE",
                validated_slots=validated_g.model_dump(mode="json", exclude_none=True),
                citations=GRIEVANCE_CITATIONS,
                redacted_transcript_summary=summary,
            )
            text = (
                f"Your grievance to {validated_g.ministry_or_department} is ready for submission "
                "on CPGRAMS (pgportal.gov.in). After submission you will receive a unique "
                "registration number to track it. Authorities should resolve grievances within "
                "21 days, and if you are not satisfied you can appeal within 30 days of closure."
            )
        return {
            "final_payload": payload.model_dump(mode="json"),
            "citations": payload.citations,
            "response": text,
            "messages": [AIMessage(text)],
            "active_flow": None,
            "pending_question": None,
            key: {},
        }
