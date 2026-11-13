"""Saved conversations for the signed-in user."""

from __future__ import annotations

import json
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from server.chat_service import engine
from server.db import Conversation, Message, User, get_db
from server.schemas import ConversationOut, MessageOut, RenameIn
from server.security import verified_user
from src.speech.bhashini import BhashiniError
from src.speech.openai_speech import SpeechError

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def owned_conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(404, {"code": "not_found", "message": "That conversation no longer exists."})
    return conv


@router.get("", response_model=list[ConversationOut])
def list_conversations(user: User = Depends(verified_user), db: Session = Depends(get_db)) -> list[ConversationOut]:
    convs = (
        db.query(Conversation)
        .filter(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(200)
        .all()
    )
    return [ConversationOut.of(c) for c in convs]


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(
    conversation_id: str, user: User = Depends(verified_user), db: Session = Depends(get_db)
) -> list[MessageOut]:
    return [MessageOut.of(m) for m in owned_conversation(db, user, conversation_id).messages]


@router.patch("/{conversation_id}", response_model=ConversationOut)
def rename(
    conversation_id: str,
    body: RenameIn,
    user: User = Depends(verified_user),
    db: Session = Depends(get_db),
) -> ConversationOut:
    conv = owned_conversation(db, user, conversation_id)
    conv.title = " ".join(body.title.split())
    db.commit()
    return ConversationOut.of(conv)


@router.delete("/{conversation_id}", status_code=204)
def delete(conversation_id: str, user: User = Depends(verified_user), db: Session = Depends(get_db)) -> Response:
    db.delete(owned_conversation(db, user, conversation_id))
    db.commit()
    return Response(status_code=204)


_speech_cache: OrderedDict[int, tuple[bytes, str]] = OrderedDict()
_SPEECH_CACHE_SIZE = 64


@router.post("/{conversation_id}/messages/{message_id}/speech")
async def message_speech(
    conversation_id: str, message_id: int, user: User = Depends(verified_user), db: Session = Depends(get_db)
) -> Response:
    """Read a reply aloud on demand ("Listen"). Uses the stored, redacted text, so masked details
    are voiced generically ("your phone number") and never reach the speech service."""
    conv = owned_conversation(db, user, conversation_id)
    msg = db.get(Message, message_id)
    if msg is None or msg.conversation_id != conv.id or msg.role != "assistant":
        raise HTTPException(404, {"code": "not_found", "message": "That message no longer exists."})
    if message_id not in _speech_cache:
        pipeline = engine.pipeline
        if pipeline is None or not pipeline.voice_enabled:
            raise HTTPException(503, {"code": "voice_off", "message": "Voice isn't available on this server."})
        language = json.loads(msg.meta_json or "{}").get("language") or "en"
        try:
            _speech_cache[message_id] = await pipeline.speak(msg.content, language)
        except (BhashiniError, SpeechError):
            raise HTTPException(
                502, {"code": "tts_failed", "message": "Couldn't read this aloud right now. Please try again."}
            ) from None
        while len(_speech_cache) > _SPEECH_CACHE_SIZE:
            _speech_cache.popitem(last=False)
    audio, mime = _speech_cache[message_id]
    return Response(content=audio, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})
