"""Send a text or voice message and stream the reply back as NDJSON events."""

from __future__ import annotations

import array
import base64
import io
import json
import logging
import math
import sys
import wave
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from config.settings import get_settings
from server.chat_service import engine, turn_events
from server.db import User
from server.schemas import ChatIn, Language
from server.security import verified_user
from src.speech.bhashini import BhashiniError
from src.speech.openai_speech import SpeechError

logger = logging.getLogger(__name__)
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
        input_mode=body.input_mode,
    )
    return StreamingResponse(_ndjson(events), media_type="application/x-ndjson", headers=_STREAM_HEADERS)


async def _read_recording(audio: UploadFile) -> bytes:
    _require_ready()
    assert engine.pipeline is not None
    if not engine.pipeline.voice_enabled:
        raise HTTPException(503, {"code": "voice_off", "message": "Voice isn't available on this server."})
    limit = get_settings().max_audio_mb * 1024 * 1024
    data = await audio.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            413, {"code": "too_large", "message": "That recording is too long. Please keep it under a minute."}
        )
    if len(data) < 1000:
        raise HTTPException(
            400, {"code": "too_short", "message": "That recording was too short. Hold the mic and speak."}
        )
    level = speech_level(data)
    if level is not None:
        logger.info("Recording: %d bytes, loudest 20 ms frame %.4f", len(data), level)  # numbers only
        if level < SILENCE_LEVEL:
            raise HTTPException(
                422,
                {
                    "code": "no_sound",
                    "message": "Your microphone sent no sound. Check that it isn't muted and that the browser "
                    "is using the right mic (the mic icon in the address bar), then try again.",
                },
            )
    return data


SILENCE_LEVEL = 0.002  # loudest 20 ms frame (RMS, full scale = 1). Quiet speech measured ~0.005+.


def speech_level(data: bytes) -> float | None:
    """RMS of the loudest 20 ms frame of a 16-bit PCM WAV, or None if it isn't one."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2:
                return None
            rate, channels = w.getframerate(), w.getnchannels()
            samples = array.array("h", w.readframes(w.getnframes()))
    except (wave.Error, EOFError):
        return None
    if sys.byteorder == "big":
        samples.byteswap()
    frame = max(1, rate * channels // 50)
    loudest = 0.0
    for i in range(0, len(samples), frame):
        chunk = samples[i : i + frame]
        loudest = max(loudest, math.sqrt(sum(s * s for s in chunk) / len(chunk)))
    return loudest / 32768


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    language: Language = Form("auto"),
    user: User = Depends(verified_user),
) -> dict[str, Any]:
    """Speech to text only. The web app shows the result for the citizen to check, edit or
    re-record before it is sent as a question. The text is returned to this user only.
    `uncertain` is true when the speech model itself was unsure (noisy or unclear audio)."""
    data = await _read_recording(audio)
    assert engine.pipeline is not None
    try:
        heard = await engine.pipeline.transcribe(base64.b64encode(data).decode("ascii"), language)
    except (BhashiniError, SpeechError) as exc:
        logger.warning("Transcription failed: %s", exc)
        raise HTTPException(
            502, {"code": "asr_failed", "message": "Couldn't understand the recording right now. Please try again."}
        ) from None
    if not heard.text:
        raise HTTPException(
            422, {"code": "not_heard", "message": "I couldn't hear any words. Please try again, closer to the mic."}
        )
    if heard.confidence is not None:
        logger.info("Transcription confidence %.2f", heard.confidence)
    if heard.unclear:
        raise HTTPException(
            422,
            {
                "code": "not_clear",
                "message": "I couldn't make out the words (too much background noise?). "
                "Please try again closer to the mic, or type your question.",
            },
        )
    return {"text": heard.text, "uncertain": heard.uncertain}


@router.post("/voice")
async def voice(
    audio: UploadFile = File(...),
    conversation_id: str | None = Form(None),
    language: Language = Form("auto"),
    speak: bool = Form(True),
    user: User = Depends(verified_user),
) -> StreamingResponse:
    """One-shot voice turn (no review step): transcribe and answer. Accepts WAV."""
    data = await _read_recording(audio)
    events = turn_events(
        user_id=user.id,
        conversation_id=conversation_id or None,
        language=language,
        speak=speak,
        audio_b64=base64.b64encode(data).decode("ascii"),
    )
    return StreamingResponse(_ndjson(events), media_type="application/x-ndjson", headers=_STREAM_HEADERS)
