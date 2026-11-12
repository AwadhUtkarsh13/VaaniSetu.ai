"""Saved conversations for the signed-in user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from server.db import Conversation, User, get_db
from server.schemas import ConversationOut, MessageOut, RenameIn
from server.security import verified_user

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
