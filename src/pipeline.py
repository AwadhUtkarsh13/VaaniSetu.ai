"""Per-turn orchestration shared by the web server and the eval runner (CLAUDE.md ADR-4).

    [voice] -> speech-to-text (OpenAI | Bhashini ASR+NMT) -> PII redaction -> LangGraph turn
            -> localisation (LLM | Bhashini NMT) -> text-to-speech (OpenAI | Bhashini)
            -> display (tokens restored locally for the same user only)

Everything is recorded in one `TurnTrace`. Raw PII stays in the per-thread `PIIVault`; what is
stored or logged is always the redacted text.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from config.settings import Settings, get_settings
from src.agent.graph import build_graph, run_turn
from src.agent.llm import AgentLLM, OpenAIAgentLLM
from src.guardrails.pii_redactor import PIIRedactor, PIIVault, RedactionEvent
from src.speech.bhashini import BHASHINI_LANGUAGES, BhashiniClient, BhashiniError
from src.speech.openai_speech import OpenAISpeech, SpeechError, speakable
from src.telemetry import TurnTrace, bind_trace, persist, stage

logger = logging.getLogger("vaanisetu.pipeline")

StatusCallback = Callable[[str], Awaitable[None]]

VOICE_UNAVAILABLE = (
    "Voice isn't available right now. Please type your message instead — I understand English, Hindi and Hinglish."
)
NOT_HEARD = "Sorry, I couldn't hear anything in that recording. Please try again, a little closer to the mic."


@dataclass
class TurnResult:
    response_text: str  # localised, PII restored for this user's display
    response_redacted: str  # localised, tokens kept: the only form that may be stored
    response_en: str  # English, redacted (what the agent produced)
    redacted_input: str
    language: str
    intent: str | None
    active_flow: str | None
    slots: dict[str, Any]
    slot_errors: dict[str, str]
    awaiting_confirmation: bool
    final_payload: dict[str, Any] | None
    citations: list[str]
    retrieved: list[dict[str, Any]]
    redactions: list[RedactionEvent]
    trace: TurnTrace
    audio_b64: str | None = None
    audio_mime: str | None = None
    transcript: str | None = None  # raw speech transcript: shown to the speaker, never stored
    degraded: list[str] = field(default_factory=list)


def _status_after(node: str, update: dict[str, Any]) -> str | None:
    if node == "intent_router":
        return {
            "POLICY_QA": "Searching the official scheme rules",
            "ELIGIBILITY_CHECK": "Noting your details",
            "GRIEVANCE_FILE": "Noting your complaint details",
        }.get(update.get("intent", ""))
    if node == "hybrid_rag_retriever":
        return "Writing an answer from the official rules"
    if node == "slot_filler" and not update.get("pending_question"):
        return "Preparing a summary for you to check"
    if node == "human_confirmation_gate":
        return "Applying the official rules"
    return None


class VaaniSetuPipeline:
    def __init__(
        self,
        graph: Any,
        llm: AgentLLM,
        redactor: PIIRedactor,
        settings: Settings,
        bhashini: BhashiniClient | None = None,
        persist_traces: bool = True,
        speech: OpenAISpeech | None = None,
    ) -> None:
        self.graph = graph
        self.llm = llm
        self.redactor = redactor
        self.settings = settings
        self.bhashini = bhashini if bhashini and bhashini.enabled else None
        self.speech = speech
        self.persist_traces = persist_traces
        self._vaults: dict[str, PIIVault] = {}
        self._closers: list[Any] = []

    @classmethod
    async def create(cls, settings: Settings | None = None, checkpointer: Any | None = None) -> VaaniSetuPipeline:
        """Wire the production components. Ingests the corpus on first run if needed."""
        from src.rag.ingest import OpenAIEmbedder, ingest, load_corpus, make_client
        from src.rag.retriever import HybridRetriever

        settings = settings or get_settings()
        llm = OpenAIAgentLLM(settings)
        embedder = OpenAIEmbedder(settings)
        client = make_client(settings)
        if not await client.collection_exists(settings.qdrant_collection):
            logger.info("Collection missing — ingesting policy corpus (first run)")
            chunks = load_corpus(settings.policies_dir)
            await ingest(chunks, client, settings.qdrant_collection, embedder)
        retriever = await HybridRetriever.from_qdrant(
            client,
            settings.qdrant_collection,
            embedder,
            rrf_k=settings.rrf_k,
            candidate_k=settings.retrieval_candidate_k,
        )
        graph = build_graph(llm, retriever, checkpointer=checkpointer, top_k=settings.retrieval_top_k)
        use_bhashini = settings.speech_provider == "bhashini" and settings.bhashini_enabled
        bhashini = BhashiniClient(settings) if use_bhashini else None
        redactor = await asyncio.to_thread(PIIRedactor, settings.pii_score_threshold)
        pipeline = cls(graph, llm, redactor, settings, bhashini, speech=OpenAISpeech(settings))
        pipeline._closers = [client.close] + ([bhashini.aclose] if bhashini else [])
        return pipeline

    async def aclose(self) -> None:
        for close in self._closers:
            await close()

    @property
    def voice_enabled(self) -> bool:
        return self.bhashini is not None or self.speech is not None

    @property
    def voice_provider(self) -> str | None:
        return "bhashini" if self.bhashini else "openai" if self.speech else None

    def vault(self, thread_id: str) -> PIIVault:
        return self._vaults.setdefault(thread_id, PIIVault())

    async def handle_turn(
        self,
        thread_id: str,
        *,
        text: str | None = None,
        audio_b64: str | None = None,
        language: str = "auto",
        want_audio: bool = False,
        on_status: StatusCallback | None = None,
    ) -> TurnResult:
        """`language`: "auto" (detect), "en", "hinglish" or a Bhashini code. Audio is base64 WAV."""
        if (text is None) == (audio_b64 is None):
            raise ValueError("Provide exactly one of text or audio_b64")

        async def status(message: str) -> None:
            if on_status is not None:
                await on_status(message)

        trace = TurnTrace(
            thread_id=thread_id,
            input_mode="voice" if audio_b64 else "text",
            language=language,
        )
        degraded: list[str] = []
        transcript: str | None = None
        with bind_trace(trace):
            # --- Stage A: speech -> text ----------
            if audio_b64 is not None:
                if not self.voice_enabled:
                    return self._early_result(trace, VOICE_UNAVAILABLE, ["voice_unavailable"])
                await status("Listening to your voice message")
                try:
                    if self.bhashini is not None:
                        lang = language if language in BHASHINI_LANGUAGES else "hi"
                        transcript, text = await self.bhashini.speech_to_english(audio_b64, lang)
                        language = lang if language == "auto" else language
                    else:
                        assert self.speech is not None
                        transcript = await self.speech.transcribe(base64.b64decode(audio_b64), "voice.wav", language)
                        text = transcript
                except (BhashiniError, SpeechError) as exc:
                    logger.warning("Speech-to-text failed: %s", exc)
                    return self._early_result(trace, VOICE_UNAVAILABLE, ["asr_failed"])
                if not text or not text.strip():
                    return self._early_result(trace, NOT_HEARD, ["empty_audio"])
            assert text is not None

            # --- Guardrail ----------
            await status("Masking personal details")
            vault = self.vault(thread_id)
            with stage("guardrail_ms", "guardrail.pii_redaction"):
                redaction = await asyncio.to_thread(self.redactor.redact, text, vault)
            trace.pii_entities_masked = redaction.masked_count

            # --- Agent ----------
            await status("Understanding your message")

            async def on_node(node: str, update: dict[str, Any]) -> None:
                message = _status_after(node, update)
                if message:
                    await status(message)

            outcome = await run_turn(self.graph, thread_id, redaction.text, on_node=on_node)
            state = outcome.state
            trace.intent = "CONFIRMATION" if outcome.resumed else state.get("intent")

            # --- Stage B: localise (+ speak) ----------
            target = state.get("user_language", "en") if language == "auto" else language
            trace.language = target
            localised, audio, mime = await self._localise(outcome.response, target, want_audio, degraded, status)

        flow = state.get("active_flow")
        slots_key = "grievance_slots" if flow == "GRIEVANCE_FILE" else "eligibility_slots"
        trace.notes.extend(degraded)
        result = TurnResult(
            response_text=vault.restore(localised),
            response_redacted=localised,
            response_en=outcome.response,
            redacted_input=redaction.text,
            language=target,
            intent=trace.intent,
            active_flow=flow,
            slots=dict(state.get(slots_key) or {}),
            slot_errors=dict(state.get("slot_errors") or {}),
            awaiting_confirmation=outcome.awaiting_confirmation,
            final_payload=state.get("final_payload"),
            citations=list(state.get("citations") or []),
            retrieved=list(state.get("retrieved") or []),
            redactions=redaction.events,
            trace=trace,
            audio_b64=audio,
            audio_mime=mime,
            transcript=transcript,
            degraded=degraded,
        )
        if self.persist_traces:
            persist(trace, self.settings.trace_log_path)
        return result

    async def _localise(
        self,
        text_en: str,
        target: str,
        want_audio: bool,
        degraded: list[str],
        status: StatusCallback,
    ) -> tuple[str, str | None, str | None]:
        if not text_en:
            return text_en, None, None
        if self.bhashini is not None and target in BHASHINI_LANGUAGES and (want_audio or target != "en"):
            try:
                if want_audio:
                    await status("Preparing the spoken reply")
                    text, audio = await self.bhashini.translate_and_speak(text_en, target)
                    return text, audio, "audio/wav"
                return await self.bhashini.translate(text_en, "en", target), None, None
            except BhashiniError as exc:
                logger.warning("Bhashini stage B failed, falling back: %s", exc)
                degraded.append("bhashini_stage_b_failed")

        if target == "en":
            text = text_en
        else:
            await status("Translating the reply")
            text = await self.llm.translate(text_en, target)  # timed under nmt_ms

        if not want_audio:
            return text, None, None
        if self.speech is None:
            degraded.append("tts_unavailable")
            return text, None, None
        await status("Preparing the spoken reply")
        try:
            mp3 = await self.speech.synthesize(speakable(text))
        except SpeechError as exc:
            logger.warning("TTS failed: %s", exc)
            degraded.append("tts_failed")
            return text, None, None
        return text, base64.b64encode(mp3).decode("ascii"), "audio/mpeg"

    def _early_result(self, trace: TurnTrace, message: str, degraded: list[str]) -> TurnResult:
        trace.notes.extend(degraded)
        return TurnResult(
            response_text=message, response_redacted=message, response_en=message,
            redacted_input="", language=trace.language, intent=None, active_flow=None, slots={},
            slot_errors={}, awaiting_confirmation=False, final_payload=None, citations=[],
            retrieved=[], redactions=[], trace=trace, degraded=degraded,
        )  # fmt: skip
