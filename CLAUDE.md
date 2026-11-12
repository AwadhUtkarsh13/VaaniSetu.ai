# Project: VaaniSetu AI — Multilingual Voice-First Citizen Welfare & Grievance Agent

## 1. Project Vision & Objective
**VaaniSetu AI** (*Vaani* = Voice, *Setu* = Bridge) is a production-grade, portfolio-ready AI engineering system modeled on Indian Digital Public Infrastructure (DPI). It enables citizens to interact via voice or text in Indian languages (primarily Hindi, Hinglish, and English, extensible to regional Bhashini-supported languages) to:
1. Query eligibility criteria, benefits, and rules for Indian government welfare schemes (starting with **PM-KISAN** and **Ayushman Bharat PM-JAY**) grounded strictly in official policy documents with section-level citations.
2. Complete multi-turn, stateful slot-filling to evaluate scheme eligibility or file a structured public grievance (modeled on **CPGRAMS**).
3. Generate a validated, PII-redacted JSON payload ready for downstream portal submission, enforcing an explicit **human-in-the-loop confirmation gate** before finalizing any action.

**Engineering Bar:** This is NOT a basic PDF-to-VectorDB wrapper. Every module must demonstrate production AI engineering rigor: deterministic LangGraph state orchestration, hybrid retrieval (BM25 + dense + RRF), Indian PII redaction middleware, per-stage latency telemetry, and reproducible evaluation benchmarks.

---

## 2. End-to-End Architecture & Data Flow
```text
[User Audio / Text Input]
       │
       ▼
[1. Speech Pipeline: OpenAI (default) or Bhashini] (Bypassed in text mode)
   ├── Stage A: ASR (Regional Audio -> Regional Text) + NMT (Regional Text -> English)
       │
       ▼
[2. Privacy & Guardrail Middleware]
   ├── Presidio + Custom Indian Regex (Mask Aadhaar, PAN, Mobile, Bank Account before LLM)
       │
       ▼
[3. LangGraph Stateful Orchestrator] (Persisted via InMemorySaver checkpointer)
   ├── Node: intent_router (Classify: POLICY_QA | ELIGIBILITY_CHECK | GRIEVANCE_FILE | OUT_OF_SCOPE)
   ├── Node: hybrid_rag_retriever (BM25 + Qdrant Dense Search + Metadata Filter + RRF)
   ├── Node: slot_filler (Extract & validate Pydantic schema fields across turns; ask 1 question/turn)
   ├── Node: human_confirmation_gate (Pause/interrupt before final submission; present summary)
   └── Node: payload_generator (Emit validated CPGRAMS / Scheme Eligibility JSON payload)
       │
       ▼
[4. Response Pipeline: AI translation or Bhashini NMT, then TTS]
   ├── Stage B: NMT (English -> User's Regional Language) + TTS (Target Text -> Base64 Audio)
       │
       ▼
[5. Observability, UI & Evaluation]
   └── React web app (FastAPI) + Per-Stage Latency Logger + Automated Evaluation Suite
```

---

## 3. Tech Stack & Implementation Specifications

* **Language & Package Management:** Python 3.11+. Pinned `requirements.txt` (`python -m venv .venv` + `pip install -r requirements.txt`, as in the author's other projects); `pyproject.toml` holds project metadata and ruff config.
* **LLM:** OpenAI via `langchain-openai`: `gpt-4.1-mini` (routing, extraction, answers, occupation classification), `gpt-4o-mini` (confirmation intent, summaries, translation), `text-embedding-3-small`. All configurable in `.env`.
* **Orchestration & State:** `langgraph`, `langchain-core`, `pydantic>=2.0`.
  * The web server persists LangGraph state with `AsyncSqliteSaver` (`data/conversations.db`) keyed by `thread_id` = conversation id, so conversations survive restarts (ADR-10). Tests and the eval use `InMemorySaver`.
  * The confirmation gate uses LangGraph `interrupt()`; the next user turn resumes it with `Command(resume=...)`.
* **Web app (ADR-9):** FastAPI (`server/`) + React 19 / TypeScript / Vite (`frontend/`), mirroring the author's CRAG / StockCast template: bcrypt passwords, server-side sessions in HttpOnly cookies, HMAC'd 6-digit email codes (verify + reset) via Gmail SMTP with console fallback, sign-in rate limiting, profile + photo. Chat replies stream as NDJSON status events; answers render as human-readable cards (progress / confirm / verdict / grievance) with the raw payload one click away.
* **Speech (ADR-11):** default provider OpenAI (`gpt-4o-mini-transcribe`, `gpt-4o-mini-tts`; `src/speech/openai_speech.py`), using the same key. The browser records 16 kHz mono WAV. Bhashini remains an optional provider (`SPEECH_PROVIDER=bhashini`): async `httpx` client in `src/speech/bhashini.py`.
  * **Two-Call Protocol:**
    1. *Pipeline Config Call (Cached):* `POST https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline` with headers `userID` and `ulcaApiKey`, requesting task sequences (`asr`, `translation`, `tts`) for a given `pipelineId` and language pair. Cache the returned `serviceId`s, `pipelineInferenceAPIEndPoint.callbackUrl`, and `inferenceApiKey` in memory so config lookup does not sit on the hot path of every request.
    2. *Pipeline Compute Call:* `POST` to `callbackUrl` (e.g., `https://dhruva-api.bhashini.gov.in/services/inference/pipeline`) with `Authorization: <inferenceApiKey>`. Ensure strict language-code alignment across chained tasks (`asr.sourceLanguage == translation.sourceLanguage` and `translation.targetLanguage == tts.sourceLanguage`).
* **Retrieval & Storage:** `qdrant-client` (local disk mode `./qdrant_data` for zero-dependency portability) + `rank_bm25` combined via **Reciprocal Rank Fusion (RRF)** with metadata filtering (`scheme_name`, `section`).
* **Privacy & Guardrails:** `presidio-analyzer` (with spaCy `en_core_web_sm`) augmented with custom Indian entity recognizers:
  * **Aadhaar Number:** 12-digit pattern (`\b[2-9]{1}[0-9]{3}\s?[0-9]{4}\s?[0-9]{4}\b`) with Verhoeff checksum validation.
  * **PAN Card:** `[A-Z]{5}[0-9]{4}[A-Z]{1}` (tightened: 4th char must be a valid holder-type letter).
  * **Indian Mobile Number:** `(?:\+91[\-\s]?)?[6-9]\d{9}`.
  * **Bank Account** (context-gated), **UPI ID**, **Email**, **Person name**.
  * Maintain a session-scoped reversible token map (`<AADHAAR_1>`, `<PHONE_1>`) only in local memory if needed for final payload formatting, ensuring raw PII is **never** sent to external LLM APIs or written to logs.
  * Stored conversation messages and applications contain only redacted text; tokens are restored only in the reply streamed back to the same user.
* **Frontend:** React (`frontend/`) with mic recording and reply playback, reply-language picker, inline cards, a "How this answer was made" panel (masked entities, intent, language, per-stage latency) and a **My applications** page with portal-ready JSON.
* **Evaluation:** `scripts/run_eval.py` runs the 30-case golden set live and writes the README table (see ADR-5). Like the author's other projects, the repository ships without a unit-test suite (ADR-15).

### Strict Coding Rules for Claude Code

1. **Simplicity & Modularity:** Write clean, idiomatic Python. Avoid premature abstractions, factory bloat, or unnecessary wrapper classes.
2. **Async First:** Use `async/await` across all external boundaries (LLM calls, Bhashini HTTP requests, Qdrant queries).
3. **Configuration:** Load all environment variables via `pydantic-settings` in `config/settings.py`. Never hardcode credentials.
6. **No hand-written vocabulary for understanding language.** Interpret meaning with the LLM, grounded in the policy documents (ADR-8, ADR-12). Hardcoded data is limited to canonical reference data (States/UTs, script names), product copy, and the local PII guardrail.
4. **Mandatory Telemetry:** Every boundary (`asr_ms`, `guardrail_ms`, `retrieval_ms`, `llm_ms`, `tts_ms`, `total_ms`) must record execution time into a structured `TurnTrace` object.
5. **Evaluation Integrity:** Never weaken the golden dataset or its expectations to make a run pass; fix the underlying implementation.

---

## 4. Repository Directory Structure

```text
vaanisetu-ai/
├── CLAUDE.md                 # Architectural & operational specification (this file)
├── PROGRESS.md               # Phase progress tracker & session handoff log
├── README.md                 # Architecture diagram, setup guide & evaluation benchmarks
├── .env.example              # Template for OpenAI, voice, email and retrieval settings
├── requirements.txt          # Pinned dependencies (pip install -r requirements.txt)
├── pyproject.toml            # Project metadata and ruff config
├── Makefile                  # install / build / start / test / eval shortcuts
├── config/
│   └── settings.py           # Pydantic BaseSettings
├── server/                   # FastAPI web app
│   ├── main.py               # App, lifespan (DB, SQLite checkpointer, background warm-up), SPA serving
│   ├── db.py                 # Users, sessions, email codes, conversations, messages, applications
│   ├── security.py           # bcrypt, session cookies, rate limiting
│   ├── email_codes.py        # HMAC'd one-time codes;  mailer.py: Gmail SMTP / console fallback
│   ├── chat_service.py       # Engine (shared pipeline), turn streaming, cards, redacted persistence
│   └── routes/               # auth, profile, conversations, chat (text + voice), applications
├── frontend/                 # React + TypeScript (Vite): auth pages, chat, applications, profile
├── data/
│   ├── raw_policies/         # Curated PM-KISAN, Ayushman Bharat & CPGRAMS rule docs (.md)
│   └── eval_dataset.json     # 30 golden Q&A + Hinglish/noisy slot-filling test cases
├── src/
│   ├── __init__.py
│   ├── telemetry.py          # Per-stage latency & token usage tracker (TurnTrace)
│   ├── pipeline.py           # Per-turn orchestration: speech -> guardrail -> graph -> speech
│   ├── guardrails/
│   │   └── pii_redactor.py   # Presidio + custom Indian PII recognizers & masking map
│   ├── speech/
│   │   ├── openai_speech.py  # Default voice: OpenAI transcription + TTS
│   │   └── bhashini.py       # Optional: cached ULCA config lookup + async ASR/NMT/TTS inference
│   ├── rag/
│   │   ├── ingest.py         # Section-aware chunking with scheme & rule metadata
│   │   └── retriever.py      # Hybrid BM25 + Qdrant dense search with RRF
│   ├── agent/
│   │   ├── state.py          # LangGraph state TypedDict & Pydantic slot schemas
│   │   ├── eligibility.py    # Deterministic, citation-backed eligibility rule engine
│   │   ├── llm.py            # OpenAI structured-output calls (router, extractor, answerer)
│   │   ├── prompts.py        # Grounded, citation-enforcing system prompts
│   │   ├── nodes.py          # Router, RAG, Slot-Filler, Confirmation Gate, Payload Builder
│   │   └── graph.py          # Compiled LangGraph StateGraph (+ run_turn with progress callbacks)
└── scripts/
    └── run_eval.py           # Automated benchmark runner (live, 30 golden cases)
```

---

## 5. Phased Build Roadmap

### Phase 1: Scaffold, PII Guardrails & Hybrid RAG Ingestion
* Initialize repository layout, `pyproject.toml`, `.env.example`, `PROGRESS.md`, and `config/settings.py` (originally `src/config.py`).
* Implement `src/guardrails/pii_redactor.py` with custom Indian PII recognizers (Aadhaar, PAN, `+91` mobile) and comprehensive unit tests (later removed, see ADR-15).
* Create structured, realistic policy documents in `data/raw_policies/` covering **PM-KISAN** (eligibility, exclusions like institutional landholders/income tax payers, installment rules) and **Ayushman Bharat PM-JAY** (SECC criteria, hospital cover, exclusions).
* Implement `src/rag/ingest.py` and `src/rag/retriever.py` (Hybrid BM25 + Qdrant RRF).

### Phase 2: LangGraph Stateful Orchestrator (Text-First)
* Define strict Pydantic schemas in `src/agent/state.py`:
  * `EligibilitySlots`: `scheme_name`, `state`, `occupation`, `land_holding_hectares`, `annual_income_inr`, `is_income_tax_payer`, `has_aadhaar_linked_bank` (+ `age_years` for PM-JAY, see ADR-3).
  * `GrievanceSlots`: `ministry_or_department`, `state`, `district`, `scheme_reference`, `incident_summary`, `grievance_category`, `urgency`.
  * `FinalActionPayload`: `action_type`, `validated_slots`, `eligibility_verdict` (if applicable), `citations`, `redacted_transcript_summary`, `timestamp_utc`.
* Implement nodes in `src/agent/nodes.py` and compile `src/agent/graph.py` with `InMemorySaver`:
  * `intent_router` accurately routes policy questions, eligibility checks, grievance filings, or out-of-scope requests.
  * `slot_filler` extracts multiple slots if provided in one turn, validates types/ranges, and asks **at most one clear, polite clarifying question per turn** for missing required fields.
  * `human_confirmation_gate` pauses before final payload generation, summarizes collected details, and requires explicit user confirmation (`yes`/`confirm`/`haan`).
* Verify multi-turn flows and edge cases.

### Phase 3: Bhashini Multilingual Voice Pipeline & Telemetry
* Implement `src/speech/bhashini.py` supporting `hi` (Hindi), `en` (English), and regional fallback handling, with in-memory config caching and graceful degradation to text mode if Bhashini credentials are missing or upstream times out.
* Wire `src/telemetry.py` across every node and API boundary so every turn produces a structured latency breakdown.

### Phase 4: Web UI & Reproducible Evaluation Benchmark
* (Originally Chainlit; replaced by FastAPI + React with accounts in Phase 5, see ADR-9.) Support both text and voice messages, displaying:
  1. Conversational response (text + playable TTS audio when voice mode is active).
  2. Expandable steps/side panel showing **Current Intent**, **Extracted JSON Slots**, **PII Redaction Masking Log**, and **Per-Stage Latency (`ms`)**.
* Create `data/eval_dataset.json` (30 test cases spanning clean English policy Q&A, Hinglish code-mixed slot-filling, edge-case exclusions, and adversarial/out-of-scope prompts).
* Implement `scripts/run_eval.py` to compute Faithfulness, Answer Relevancy, and Slot-Extraction Accuracy, writing results to a Markdown benchmark table for `README.md`.

### Phase 5: Product Web App (FastAPI + React, accounts, persistence)
* Port the author's CRAG template: `server/` (auth with email codes, sessions, profile) and `frontend/` (React + Vite).
* Streaming chat and voice endpoints; human-readable cards instead of raw JSON; saved applications; SQLite checkpointer.
* OpenAI as the default speech provider; replies in the user's language and script.
* Policy-grounded classification instead of hand-written vocabulary (ADR-12).

---

## 6. Session & Workflow Protocol for Claude Code

1. **Context Loading:** At the start of every session, read `CLAUDE.md` and `PROGRESS.md` first.
2. **Plan Before Coding:** Always enter Plan Mode at the start of a phase, list the exact files to create or modify, and wait for user confirmation before writing code.
3. **Verification Gate:** Run `python -m ruff check .`, `cd frontend && npm run build` and the live evaluation `python scripts/run_eval.py` at the end of every phase, plus a manual run of the app. Do not mark a phase complete until all of them are clean.
4. **Handoff Logging:** Before ending a session or clearing context (`/clear`), update `PROGRESS.md` with completed files, passing test commands, known limitations, and the exact starting point for the next phase.

---

## 7. Architecture Decision Records (deviations & clarifications from the original brief)

* **ADR-1 — Own redaction step, no `presidio-anonymizer`.** Presidio's anonymizer applies operators right-to-left, which would number tokens in reverse (`<PHONE_2>` before `<PHONE_1>`). Redaction uses `presidio-analyzer` for detection plus a ~30-line overlap resolver that assigns stable left-to-right tokens through a session-scoped `PIIVault`.
* **ADR-2 — Deterministic eligibility engine.** Eligibility verdicts are computed by `src/agent/eligibility.py` (pure Python rules), never by the LLM. Every rule cites a chunk id that exists in the policy corpus (enforced by a test). The LLM only extracts slots and phrases responses.
* **ADR-3 — `age_years` added to `EligibilitySlots`.** PM-JAY's 2024 expansion covers all citizens aged 70+, so age is required for a PM-JAY verdict. Required slots differ per scheme (`REQUIRED_SLOTS` in `state.py`).
* **ADR-4 — `src/pipeline.py` and `src/agent/llm.py` added.** `pipeline.py` is the single per-turn entry point (speech → guardrail → graph → speech + telemetry), shared by the UI and the eval runner so the UI holds no business logic. `llm.py` isolates OpenAI calls behind a small `AgentLLM` protocol so the agent is not tied to one LLM client.
* **ADR-5 — In-house LLM-judge metrics instead of the `ragas`/`deepeval` packages.** Both frameworks pull large dependency trees and have frequent breaking API changes. `scripts/run_eval.py` implements the RAGAS metric definitions directly (faithfulness = supported claims / total claims; answer relevancy = LLM-judged 0–1 relevance) plus deterministic intent accuracy, slot-extraction accuracy, verdict accuracy and retrieval hit-rate@k.
* **ADR-6 — Query rewriting in the router.** The router emits a standalone English search query, so Hinglish / multi-turn follow-ups still hit BM25 over English policy text.
* **ADR-8 — Semantic understanding via LLM, not keyword lists.** Anything that interprets language — confirmation replies (yes / haan / "theek hai, bhej do" / corrections), occupation mapping, state normalisation, language detection — is done by a small OpenAI model (`OPENAI_SMALL_MODEL`) or the main model's structured output, on already-redacted text. **Exception:** PII detection stays local (regex + Verhoeff + spaCy). Asking a remote LLM to *find* PII would require sending the raw, unmasked text to it, which is exactly what the guardrail exists to prevent. Its small Hinglish function-word list only suppresses spaCy false positives.
* **ADR-7 — Response localisation.** Nodes produce English text. If Bhashini is configured and the user language is a Bhashini language, Stage B NMT translates it; otherwise (e.g. Hinglish, or no Bhashini keys) the LLM performs the translation. The translator is given the language *and script* ("Hindi, written in Devanagari script"), because a bare `hi` code produced romanised Hindi.
* **ADR-9 — FastAPI + React instead of Chainlit.** Requested by the author to match their CRAG / StockCast projects (structure, README style, login system). `src/pipeline.py` was already the single entry point, so the UI swap touched no agent code.
* **ADR-10 — Persistent conversations.** `AsyncSqliteSaver` replaces `InMemorySaver` in the server; a paused confirmation survives a restart (verified live). The `PIIVault` stays in memory by design: after a restart, old tokens simply stay masked.
* **ADR-11 — OpenAI as the default speech provider.** Bhashini needs separate registration; OpenAI's transcription/TTS models work with the existing key, handle Hindi/Hinglish, and cost well under a rupee per minute. Sarvam AI (Indian-accent tuned, ₹100 free credit at sign-up) is a possible future provider. TTS input is made "speakable": PII tokens are voiced generically ("your phone number"), never restored, so no raw PII reaches the TTS API.
* **ADR-12 — Policy-grounded classification, no hand-written vocabulary.** Occupation categories are defined abstractly; which jobs belong to which category is decided by the LLM reading the official rule text (PM-JAY §3.2–3.6, PM-KISAN §4) inserted at start-up from the corpus (`prompts.policy_rules`). When extraction is unsure, a second structured classifier (translate → list closest named occupations → decide) runs on the citizen's exact words. Grievance routing / category / urgency likewise come from CPGRAMS §3.1 and §6. The PII layer's domain allow-list is derived from the corpus vocabulary. Measured live: 10/10 occupations end-to-end, 18/20 on a harder classifier set without any examples in the prompt.
* **ADR-13 — 8 retrieved passages.** Measured on 22 real questions with the live router: 8 passages lift retrieval hit-rate from 19/22 to 20/22 over 5; adding a keyword-rewrite query or the original wording gave no further gain, so it was not kept. (A user `.env` copied from the old template still pinned `RETRIEVAL_TOP_K=5`; fixed.)
* **ADR-14 — Corrective retrieval (as in the author's CRAG project).** When the answerer finds no supported answer in the first 8 passages, `grounded_answer` widens the search once to 16 and retries. It fixes answers sitting just below the cut-off (strong in dense search, weak in BM25 because the citizen's wording differs from the rules, e.g. "paisa" vs "benefit"), costs one extra call only on misses, and genuinely unanswerable questions still refuse. Logged as `retrieval_widened` in the turn trace.
* **ADR-15 — No unit-test suite in the repository.** Removed at the author's request to match the CRAG / StockCast repositories. Before removal the suite had 150 passing offline tests (guardrails, retrieval, eligibility rules, agent flows, speech, pipeline, web API); the final build was also verified with the live 30-case evaluation (all metrics 1.00), 45 API end-to-end checks and 16 real-browser checks. Quality gates are now ruff, the frontend build and the live evaluation.
