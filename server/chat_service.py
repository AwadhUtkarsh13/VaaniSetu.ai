"""Runs one VaaniSetu turn for the web app and shapes it into user-friendly events and cards.

Stream events (one JSON object per line):

* ``conversation`` - the conversation id and title (sent first)
* ``status``       - a short plain-language progress message
* ``done``         - the stored user message, the assistant reply (with its card) and any audio
* ``error``        - a friendly error message

Cards turn agent state into something a citizen can read at a glance:

* ``progress``    - details collected so far while the agent asks its questions
* ``confirm``     - the summary to approve before anything is finalised
* ``eligibility`` - the verdict, the official rules applied and a link to the saved application
* ``grievance``   - the prepared CPGRAMS complaint and where to submit it
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from config.settings import get_settings
from server.db import Application, Conversation, Message, session_factory, utcnow
from server.schemas import MessageOut
from src.agent.nodes import FIELD_LABELS, format_value
from src.agent.state import Intent, missing_slots
from src.pipeline import TurnResult, VaaniSetuPipeline
from src.rag.ingest import load_corpus

logger = logging.getLogger(__name__)

SCHEME_LABELS = {"PM_KISAN": "PM-KISAN", "PM_JAY": "Ayushman Bharat PM-JAY"}
CPGRAMS_URL = "https://pgportal.gov.in"


class Engine:
    """The shared agent pipeline, loaded in the background when the server starts."""

    def __init__(self) -> None:
        self.pipeline: VaaniSetuPipeline | None = None
        self.ready = False
        self.error: str | None = None
        self.labels: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self, checkpointer: Any) -> None:
        settings = get_settings()
        try:
            if not settings.openai_enabled:
                self.error = "missing_openai_key"
                logger.error("OPENAI_API_KEY is not set; the assistant cannot answer.")
                return
            self.labels = {c.chunk_id: c.label for c in load_corpus(settings.policies_dir)}
            self.pipeline = await VaaniSetuPipeline.create(settings, checkpointer=checkpointer)
            self.ready = True
            logger.info("Assistant ready (voice: %s)", self.pipeline.voice_provider or "off")
        except Exception as exc:  # surfaced through /api/health
            logger.exception("Warm-up failed")
            self.error = type(exc).__name__

    def use(self, pipeline: VaaniSetuPipeline, labels: dict[str, str]) -> None:
        """Install an already-built pipeline (e.g. a custom one) instead of warming up."""
        self.pipeline, self.labels, self.ready, self.error = pipeline, labels, True, None

    def lock(self, conversation_id: str) -> asyncio.Lock:
        return self._locks.setdefault(conversation_id, asyncio.Lock())

    async def stop(self) -> None:
        if self.pipeline is not None:
            await self.pipeline.aclose()


engine = Engine()


# --- Cards & metadata ----------
def citation_list(ids: list[str]) -> list[dict[str, str]]:
    return [{"id": i, "label": engine.labels.get(i, i)} for i in dict.fromkeys(ids)]


def _items(slots: dict[str, Any]) -> list[dict[str, str]]:
    return [{"label": FIELD_LABELS.get(k, k), "value": format_value(v)} for k, v in slots.items() if v is not None]


def build_card(result: TurnResult) -> dict[str, Any] | None:
    payload = result.final_payload
    if payload and payload["action_type"] == "SCHEME_ELIGIBILITY_CHECK":
        verdict = payload["eligibility_verdict"]
        return {
            "kind": "eligibility",
            "application_id": payload["payload_id"],
            "scheme": SCHEME_LABELS.get(verdict["scheme_name"], verdict["scheme_name"]),
            "verdict": verdict["verdict"],
            "reasons": verdict["reasons"],
            "next_steps": verdict["next_steps"],
            "items": _items(payload["validated_slots"]),
            "citations": citation_list(verdict["citations"]),
        }
    if payload and payload["action_type"] == "CPGRAMS_GRIEVANCE":
        slots = payload["validated_slots"]
        return {
            "kind": "grievance",
            "application_id": payload["payload_id"],
            "department": slots.get("ministry_or_department"),
            "summary": slots.get("incident_summary"),
            "items": _items({k: v for k, v in slots.items() if k != "incident_summary"}),
            "portal_url": CPGRAMS_URL,
            "citations": citation_list(payload["citations"]),
        }
    if result.awaiting_confirmation:
        return {"kind": "confirm", "flow": result.active_flow, "items": _items(result.slots)}
    # Progress only on replies that belong to the check (not on a question answered mid-check).
    if result.active_flow and result.slots and result.intent == result.active_flow:
        remaining = len(missing_slots(Intent(result.active_flow), result.slots))
        return {
            "kind": "progress",
            "flow": result.active_flow,
            "items": _items(result.slots),
            "remaining": remaining,
        }
    return None


def assistant_meta(result: TurnResult) -> dict[str, Any]:
    t = result.trace
    return {
        "intent": result.intent,
        "language": result.language,
        "input_mode": t.input_mode,
        "card": build_card(result),
        "citations": citation_list(result.citations),
        "awaiting_confirmation": result.awaiting_confirmation,
        "masked": [{"entity_type": e.entity_type, "token": e.token} for e in result.redactions],
        "timings": {k: v for k, v in t.stages.items() if v},
        "llm_calls": t.llm_calls,
        "tokens": t.prompt_tokens + t.completion_tokens,
        "degraded": result.degraded,
    }


def _title(text: str) -> str:
    title = " ".join(text.split())
    return title if len(title) <= 60 else title[:57].rstrip() + "..."


def _application_title(payload: dict[str, Any]) -> str:
    slots = payload["validated_slots"]
    if payload["action_type"] == "SCHEME_ELIGIBILITY_CHECK":
        return f"{SCHEME_LABELS.get(slots.get('scheme_name', ''), 'Scheme')} eligibility check"
    category = format_value(slots.get("grievance_category", "other")).capitalize()
    where = slots.get("district") or slots.get("state") or ""
    return f"Grievance: {category}" + (f" ({where})" if where else "")


# --- One turn ----------
def open_conversation(user_id: str, conversation_id: str | None, first_text: str) -> Conversation:
    """Return the user's conversation, or create one. Raises LookupError if not theirs."""
    with session_factory()() as db:
        if conversation_id:
            conv = db.get(Conversation, conversation_id)
            if conv is None or conv.user_id != user_id:
                raise LookupError(conversation_id)
            return conv
        conv = Conversation(user_id=user_id, title=_title(first_text))
        db.add(conv)
        db.commit()
        return conv


def save_turn(
    user_id: str, conv: Conversation, result: TurnResult, is_new: bool
) -> tuple[MessageOut | None, MessageOut, str]:
    """Persist the redacted turn (and any finished application).

    Returns the stored user message, the stored reply and the conversation title.
    """
    with session_factory()() as db:
        stored_conv = db.get(Conversation, conv.id)
        assert stored_conv is not None
        user_msg = None
        if result.redacted_input:
            user_msg = Message(
                conversation_id=conv.id,
                role="user",
                content=result.redacted_input,
                meta_json=json.dumps(
                    {
                        "input_mode": result.trace.input_mode,
                        "masked": [{"entity_type": e.entity_type, "token": e.token} for e in result.redactions],
                    }
                ),
            )
            db.add(user_msg)
            if is_new and result.trace.input_mode == "voice":
                stored_conv.title = _title(result.redacted_input)
        meta = assistant_meta(result)
        reply = Message(
            conversation_id=conv.id,
            role="assistant",
            content=result.response_redacted,
            meta_json=json.dumps(meta, ensure_ascii=False),
        )
        db.add(reply)
        payload = result.final_payload
        if payload:
            verdict = (payload.get("eligibility_verdict") or {}).get("verdict")
            db.add(
                Application(
                    id=payload["payload_id"],
                    user_id=user_id,
                    conversation_id=conv.id,
                    action_type=payload["action_type"],
                    title=_application_title(payload),
                    verdict=verdict,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                )
            )
        stored_conv.updated_at = utcnow()
        db.commit()
        return (
            (MessageOut.of(user_msg) if user_msg else None),
            MessageOut.of(reply),
            stored_conv.title,
        )


async def turn_events(
    *,
    user_id: str,
    conversation_id: str | None,
    language: str,
    speak: bool,
    text: str | None = None,
    audio_b64: str | None = None,
    input_mode: str = "text",
) -> AsyncIterator[dict[str, Any]]:
    """Run one turn and yield stream events. The caller has already checked readiness."""
    pipeline = engine.pipeline
    assert pipeline is not None
    try:
        conv = open_conversation(user_id, conversation_id, text or "Voice conversation")
    except LookupError:
        yield {
            "type": "error",
            "code": "not_found",
            "message": "That conversation no longer exists.",
        }
        return
    is_new = conversation_id is None
    yield {"type": "conversation", "id": conv.id, "title": conv.title}

    lock = engine.lock(conv.id)
    if lock.locked():
        yield {
            "type": "error",
            "code": "busy",
            "message": "Please wait for the current reply to finish.",
        }
        return
    async with lock:
        queue: asyncio.Queue[str] = asyncio.Queue()
        task = asyncio.create_task(
            pipeline.handle_turn(
                conv.id,
                text=text,
                audio_b64=audio_b64,
                language=language,
                want_audio=speak,
                on_status=queue.put,
                input_mode=input_mode,
            )
        )
        while not task.done():
            try:
                yield {"type": "status", "text": await asyncio.wait_for(queue.get(), timeout=0.25)}
            except TimeoutError:
                continue
        try:
            result = task.result()
        except Exception:
            logger.exception("Turn failed")
            yield {
                "type": "error",
                "code": "failed",
                "message": "Something went wrong while answering. Please try again.",
            }
            return

    user_msg, reply, title = save_turn(user_id, conv, result, is_new)
    display = reply.model_copy(update={"content": result.response_text})  # PII restored for this user only
    yield {
        "type": "done",
        "conversation": {"id": conv.id, "title": title},
        "user_message": user_msg.model_dump(mode="json") if user_msg else None,
        "transcript": result.transcript,
        "message": display.model_dump(mode="json"),
        "audio": {"data": result.audio_b64, "mime": result.audio_mime} if result.audio_b64 else None,
    }
