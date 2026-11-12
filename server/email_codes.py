"""One-time 6-digit email codes for verifying an address and resetting a password.

Codes are stored only as HMAC-SHA256 digests keyed with a server secret, expire
after a few minutes, allow a limited number of attempts, and can be re-sent at
most once per cooldown period.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from functools import lru_cache
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from config.settings import get_settings
from server.db import EmailCode, User, utcnow
from server.mailer import MailError, send_code_email

Purpose = Literal["verify", "reset"]


@lru_cache(maxsize=1)
def _secret() -> bytes:
    settings = get_settings()
    if settings.secret_key:
        return settings.secret_key.get_secret_value().encode("utf-8")
    path = settings.database_path.parent / "secret.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="ascii")
    return path.read_text(encoding="ascii").strip().encode("ascii")


def _digest(user_id: str, purpose: str, code: str) -> str:
    return hmac.new(_secret(), f"{user_id}:{purpose}:{code}".encode(), hashlib.sha256).hexdigest()


def _latest(db: Session, user: User, purpose: Purpose) -> EmailCode | None:
    return (
        db.query(EmailCode)
        .filter(EmailCode.user_id == user.id, EmailCode.purpose == purpose)
        .order_by(EmailCode.id.desc())
        .first()
    )


def seconds_until_resend(db: Session, user: User, purpose: Purpose) -> int:
    """How long before another code may be sent (0 if allowed now)."""
    latest = _latest(db, user, purpose)
    if latest is None:
        return 0
    wait = get_settings().email_resend_seconds - (utcnow() - latest.created_at).total_seconds()
    return max(0, int(wait + 0.999))


def has_active_code(db: Session, user: User, purpose: Purpose) -> bool:
    latest = _latest(db, user, purpose)
    return bool(latest and latest.used_at is None and latest.expires_at > utcnow())


def issue_code(db: Session, user: User, purpose: Purpose) -> None:
    """Create a new code (invalidating earlier ones) and email it.

    Raises:
        HTTPException 429: when asked again within the resend cooldown.
        HTTPException 502: when the email could not be sent.
    """
    wait = seconds_until_resend(db, user, purpose)
    if wait:
        raise HTTPException(
            429,
            {
                "code": "resend_too_soon",
                "message": f"Please wait {wait} seconds before asking for a new code.",
                "retry_after": wait,
            },
        )
    settings = get_settings()
    now = utcnow()
    db.query(EmailCode).filter(
        EmailCode.user_id == user.id, EmailCode.purpose == purpose, EmailCode.used_at.is_(None)
    ).update({EmailCode.used_at: now})
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        EmailCode(
            user_id=user.id,
            purpose=purpose,
            code_hash=_digest(user.id, purpose, code),
            created_at=now,
            expires_at=now + timedelta(minutes=settings.email_code_minutes),
        )
    )
    db.commit()
    try:
        send_code_email(user.email, user.name, code, purpose)
    except MailError:
        raise HTTPException(
            502,
            {
                "code": "email_failed",
                "message": "We couldn't send the email right now. Please try again shortly.",
            },
        ) from None


def check_code(db: Session, user: User, purpose: Purpose, code: str) -> None:
    """Consume a valid code, or raise a user-friendly 400."""
    settings = get_settings()
    latest = _latest(db, user, purpose)
    if latest is None or latest.used_at is not None:
        raise HTTPException(400, {"code": "no_code", "message": "This code is no longer valid. Ask for a new one."})
    if latest.expires_at <= utcnow():
        raise HTTPException(400, {"code": "code_expired", "message": "This code has expired. Ask for a new one."})
    if latest.attempts >= settings.email_code_attempts:
        raise HTTPException(
            400,
            {
                "code": "too_many_attempts",
                "message": "Too many wrong attempts. Ask for a new code.",
            },
        )
    if not hmac.compare_digest(latest.code_hash, _digest(user.id, purpose, code.strip())):
        latest.attempts += 1
        db.commit()
        left = settings.email_code_attempts - latest.attempts
        message = "That code isn't right." + (f" {left} attempt{'s' if left != 1 else ''} left." if left else "")
        raise HTTPException(400, {"code": "wrong_code", "message": message})
    latest.used_at = utcnow()
    db.commit()
