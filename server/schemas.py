"""Request and response models with user-facing validation messages."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from server.db import Application, Conversation, Message, User

Gender = Literal["female", "male", "non_binary", "other", "prefer_not_to_say"]
Language = Literal["auto", "en", "hinglish", "hi", "mr", "bn", "ta", "te", "gu", "kn", "ml", "pa", "or", "ur", "as"]

_PHONE_ALLOWED = re.compile(r"^\+?[0-9 ()\-]{7,20}$")


def normalize_phone(value: str) -> str:
    value = value.strip()
    if not _PHONE_ALLOWED.match(value):
        raise ValueError("Enter a valid phone number, for example +91 98765 43210.")
    digits = re.sub(r"\D", "", value)
    if not 7 <= len(digits) <= 15:
        raise ValueError("A phone number has between 7 and 15 digits.")
    return ("+" if value.startswith("+") else "") + digits


def check_password(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Use at least 8 characters.")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Use at most 72 characters.")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("Include at least one letter and one number.")
    return value


def clean_name(value: str) -> str:
    value = " ".join(value.split())
    if not 2 <= len(value) <= 80:
        raise ValueError("Enter your full name (2 to 80 characters).")
    return value


class EmailIn(BaseModel):
    email: EmailStr


class CodeIn(BaseModel):
    code: str

    @field_validator("code")
    @classmethod
    def _six_digits(cls, v: str) -> str:
        v = re.sub(r"\s", "", v)
        if not re.fullmatch(r"\d{6}", v):
            raise ValueError("Enter the 6-digit code from the email.")
        return v


class ResetIn(CodeIn):
    email: EmailStr
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _valid_new_password(cls, v: str) -> str:
        return check_password(v)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class SignupIn(BaseModel):
    name: str
    email: EmailStr
    phone: str
    password: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return clean_name(v)

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, v: str) -> str:
        return normalize_phone(v)

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        return check_password(v)


class ProfileUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    dob: date | None = None
    gender: Gender | None = None
    preferred_language: Language | None = None

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str | None) -> str | None:
        return v if v is None else clean_name(v)

    @field_validator("phone")
    @classmethod
    def _valid_phone(cls, v: str | None) -> str | None:
        return v if v is None else normalize_phone(v)

    @field_validator("dob")
    @classmethod
    def _dob(cls, v: date | None) -> date | None:
        if v is None:
            return v
        if v > date.today():
            raise ValueError("Date of birth can't be in the future.")
        if v.year < 1900:
            raise ValueError("Enter a valid date of birth.")
        return v


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _valid_new_password(cls, v: str) -> str:
        return check_password(v)


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    phone: str
    dob: date | None
    gender: Gender | None
    preferred_language: str
    has_photo: bool
    photo_version: int
    email_verified: bool
    created_at: datetime

    @classmethod
    def of(cls, user: User) -> UserOut:
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            phone=user.phone,
            dob=user.dob,
            gender=user.gender,  # type: ignore[arg-type]
            preferred_language=user.preferred_language or "auto",
            has_photo=bool(user.photo_filename),
            photo_version=int(user.updated_at.timestamp()),
            email_verified=user.email_verified_at is not None,
            created_at=user.created_at,
        )


class ConversationOut(BaseModel):
    id: str
    title: str
    updated_at: datetime

    @classmethod
    def of(cls, conv: Conversation) -> ConversationOut:
        return cls(id=conv.id, title=conv.title, updated_at=conv.updated_at)


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    meta: dict[str, Any]
    created_at: datetime

    @classmethod
    def of(cls, msg: Message) -> MessageOut:
        return cls(
            id=msg.id,
            role=msg.role,  # type: ignore[arg-type]
            content=msg.content,
            meta=json.loads(msg.meta_json or "{}"),
            created_at=msg.created_at,
        )


class ApplicationOut(BaseModel):
    id: str
    action_type: str
    title: str
    verdict: str | None
    conversation_id: str | None
    created_at: datetime

    @classmethod
    def of(cls, app: Application) -> ApplicationOut:
        return cls(
            id=app.id,
            action_type=app.action_type,
            title=app.title,
            verdict=app.verdict,
            conversation_id=app.conversation_id,
            created_at=app.created_at,
        )


class ApplicationDetail(ApplicationOut):
    payload: dict[str, Any]

    @classmethod
    def of(cls, app: Application) -> ApplicationDetail:  # type: ignore[override]
        return cls(**ApplicationOut.of(app).model_dump(), payload=json.loads(app.payload_json))


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    language: Language = "auto"
    speak: bool = False

    @field_validator("message")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Type a message first.")
        return v


class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
