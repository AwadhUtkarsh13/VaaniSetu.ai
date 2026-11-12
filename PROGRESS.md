# PROGRESS — VaaniSetu AI

## Status (2026-11-12) — ready to commit and push

| Phase | Status |
|---|---|
| 1. Scaffold, PII guardrails, hybrid RAG ingestion | ✅ Complete |
| 2. LangGraph stateful orchestrator | ✅ Complete |
| 3. Voice pipeline & telemetry | ✅ Complete (OpenAI speech live-verified; Bhashini via mocked backend) |
| 4. UI & evaluation benchmark | ✅ Complete |
| 5. Product web app: FastAPI + React, accounts, persistence (replaces Chainlit) | ✅ Complete |

## Final verification (all against real OpenAI unless noted)

| Check | Result |
|---|---|
| Offline unit tests (before removal, ADR-15) | 150 passed |
| `python -m ruff check .` / `ruff format --check .` | clean |
| `cd frontend && npm run build` (strict TypeScript) | clean |
| `scripts/run_eval.py` — 30 golden cases, 58 turns | **all 9 quality metrics 1.00**, p50 3.5 s / p95 6.1 s per turn |
| API end-to-end against the running server | **45/45** (accounts, email codes, EN/Hinglish/Hindi answers, PII masking, cards, edit-at-gate, grievance, voice in/out, password change/reset, cross-user isolation) |
| Browser end-to-end (real Chrome, Playwright, fake mic playing a spoken question) | **16/16** (sign-up → verify → cited Hinglish answer → confirm card → verdict card → saved application → voice → profile → sign-out → mobile, no console errors) |
| Restart persistence | a paused confirmation resumed correctly after a full server restart |

## What changed in Phase 5
- **Structure aligned with the author's CRAG / StockCast projects:** `server/` (FastAPI), `frontend/` (React 19 + Vite + TS), `config/settings.py`, pinned `requirements.txt`, `Makefile`, README in the same style.
- **Login system ported from CRAG:** sign-up, 6-digit email verification (Gmail SMTP or console), sign-in with rate limiting, forgot/reset password, profile with photo, password change. Plus a reply-language preference.
- **Readable answers instead of raw JSON:** progress / confirm / verdict / grievance cards, citation chips, "🔒 Aadhaar hidden" chips, a "How this answer was made" panel, and a **My applications** page with portal-ready JSON (download/copy).
- **Replies in the user's language and script:** Hinglish in → Hinglish out, Devanagari Hindi in → Devanagari out (fixed `DEFAULT_LANGUAGE=en` in `.env`; the translator now gets language + script).
- **Voice without Bhashini:** OpenAI `gpt-4o-mini-transcribe` + `gpt-4o-mini-tts` with the existing key; the browser records 16 kHz WAV; spoken questions get spoken answers. Bhashini stays optional.
- **No hand-written vocabulary (ADR-12):** occupation classification and grievance routing are grounded in the official rule text loaded from the corpus, with a step-by-step fallback classifier; the PII allow-list is derived from the corpus vocabulary; income is reported as stated and converted by code.
- **Retrieval:** 8 passages (measured) + one corrective widening to 16 when the first pass can't answer (ADR-13/14).
- **Persistence:** SQLite LangGraph checkpointer; stored messages and applications are redacted only.

## Local environment notes
- `.env` (not committed) — corrected during this session: `OPENAI_SMALL_MODEL=gpt-4o-mini` (gpt-4.1-nano is not on this account), `DEFAULT_LANGUAGE=auto`, `RETRIEVAL_TOP_K=8`.
- Email: `SMTP_*` copied from the CRAG / my-project `.env` (same Gmail account), sender shown as "VaaniSetu AI". Verified by sending a settings-check email and a sample verification-code email (both accepted by Gmail).
- The unit-test suite was removed from the repository at the author's request (ADR-15); a backup existed only in the session scratch folder.

## Next steps
1. `git commit` and `git push` to https://github.com/AwadhUtkarsh13/VaaniSetu.ai (remote has one "Initial commit" with a README; merge on top of it).
2. Optional: Bhashini or Sarvam AI keys as alternative speech providers.
3. Harder benchmark cases (it scores 1.00, so it no longer discriminates): ASR-noisy text, topic switches mid-flow, several PII types per turn.
4. Deployment: HTTPS (`COOKIE_SECURE=true`), Qdrant server mode, Postgres checkpointer.

## Known limitations
- The `PIIVault` is in memory: after a restart, earlier masked values stay masked in the chat history (by design, never persisted).
- Qdrant local mode allows one process per `qdrant_data/`; stop the app before running the eval or live tests (or set `QDRANT_URL`).
- Voice audio itself is sent to the speech provider; only the transcript is masked before the agent sees it.
- Bhashini integration is tested against an emulator of the documented API, not live credentials.
- Policy documents are curated summaries for demonstration; verify facts on the official portals.
