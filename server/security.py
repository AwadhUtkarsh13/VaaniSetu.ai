"""Password hashing, session cookies and sign-in rate limiting."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from config.settings import get_settings
from server.db import AuthSession, User, get_db, utcnow

SESSION_COOKIE = "vaanisetu_session"


def hash_password(password: str) -> str:
    """Return a bcrypt hash (cost 12)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time comparison of ``password`` against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def start_session(db: Session, user: User, response: Response) -> None:
    """Create a server-side session and set it as an HttpOnly cookie."""
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token_hash=_token_hash(token),
            user_id=user.id,
            expires_at=utcnow() + timedelta(days=settings.session_days),
        )
    )
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def end_session(db: Session, request: Request, response: Response) -> None:
    """Delete the current session (if any) and clear the cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.query(AuthSession).filter(AuthSession.token_hash == _token_hash(token)).delete()
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


def end_all_sessions(db: Session, user: User, keep_request: Request | None = None) -> None:
    """Sign the user out everywhere, optionally keeping the current session."""
    keep = (
        _token_hash(keep_request.cookies[SESSION_COOKIE])
        if keep_request and SESSION_COOKIE in keep_request.cookies
        else None
    )
    query = db.query(AuthSession).filter(AuthSession.user_id == user.id)
    if keep:
        query = query.filter(AuthSession.token_hash != keep)
    query.delete()
    db.commit()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: the signed-in user, or 401."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = db.get(AuthSession, _token_hash(token))
        if session and session.expires_at > utcnow():
            return session.user
        if session:
            db.delete(session)
            db.commit()
    raise HTTPException(status_code=401, detail={"code": "not_signed_in", "message": "Please sign in to continue."})


def verified_user(user: User = Depends(current_user)) -> User:
    """FastAPI dependency: a signed-in user whose email address is confirmed, or 403."""
    if user.email_verified_at is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "email_not_verified",
                "message": "Please confirm your email address to continue.",
            },
        )
    return user


class RateLimiter:
    """Sliding-window limiter for failed sign-in attempts (per email and per IP)."""

    def __init__(self, max_attempts: int = 8, window_s: int = 900) -> None:
        self.max_attempts = max_attempts
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_s:
            hits.popleft()
        return hits

    def blocked(self, *keys: str) -> bool:
        now = time.monotonic()
        with self._lock:
            return any(len(self._prune(k, now)) >= self.max_attempts for k in keys)

    def record_failure(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            for k in keys:
                self._prune(k, now).append(now)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._hits.pop(k, None)


login_limiter = RateLimiter()
