"""Send a text or voice message and stream the reply back as NDJSON events."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from config.settings import get_settings
from server.chat_service import engine, turn_events
from server.db import User
from server.schemas import ChatIn, Language
from server.security import verified_user

router = APIRouter(prefix="/api/chat", tags=["chat"])

_STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _require_ready() -> None:
    if engine.error == "missing_openai_key":
        raise HTTPException(
            503,
            {
                "code": "not_configured",
                "message": "The assistant isn't set up yet: an OpenAI API key is missing.",
            },
        )
    if not engine.ready:
        raise HTTPException(
            503,
            {"code": "not_ready", "message": "The assistant is still starting. Try again shortly."},
        )


async def _ndjson(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    async for event in events:
        yield json.dumps(event, ensure_ascii=False) + "\n"


@router.post("")
async def chat(body: ChatIn, user: User = Depends(verified_user)) -> StreamingResponse:
    _require_ready()
    events = turn_events(
        user_id=user.id,
        conversation_id=body.conversation_id,
        language=body.language,
        speak=body.speak,
        text=body.message,
    )
    return StreamingResponse(_ndjson(events), media_type="application/x-ndjson", headers=_STREAM_HEADERS)


@router.post("/voice")
async def voice(
    audio: UploadFile = File(...),
    conversation_id: str | None = Form(None),
    language: Language = Form("auto"),
    speak: bool = Form(True),
    user: User = Depends(verified_user),
) -> StreamingResponse:
    """Accepts a WAV recording (the web app records 16 kHz mono PCM)."""
    _require_ready()
    assert engine.pipeline is not None
    if not engine.pipeline.voice_enabled:
        raise HTTPException(503, {"code": "voice_off", "message": "Voice isn't available on this server."})
    limit = get_settings().max_audio_mb * 1024 * 1024
    data = await audio.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            413,
            {
                "code": "too_large",
                "message": "That recording is too long. Please keep it under a minute.",
            },
        )
    if len(data) < 1000:
        raise HTTPException(
            400,
            {
                "code": "too_short",
                "message": "That recording was too short. Hold the mic and speak.",
            },
        )
    events = turn_events(
        user_id=user.id,
        conversation_id=conversation_id or None,
        language=language,
        speak=speak,
        audio_b64=base64.b64encode(data).decode("ascii"),
    )
    return StreamingResponse(_ndjson(events), media_type="application/x-ndjson", headers=_STREAM_HEADERS)
