"""Sign up, sign in, email verification, password reset and sign out."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from server.db import User, get_db, session_factory, utcnow
from server.email_codes import check_code, has_active_code, issue_code, seconds_until_resend
from server.schemas import CodeIn, EmailIn, LoginIn, ResetIn, SignupIn, UserOut
from server.security import (
    current_user,
    end_all_sessions,
    end_session,
    hash_password,
    login_limiter,
    start_session,
    verify_password,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _find(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email.lower()).one_or_none()


def _send_in_background(user_id: str, purpose: str) -> None:
    """Send a code after the response has gone out, so sign-up and sign-in don't wait on the mail server.

    Failures are only logged: the verification screen lets the user ask for a new code.
    """
    with session_factory()() as db:
        user = db.get(User, user_id)
        if user is None:
            return
        try:
            issue_code(db, user, purpose)  # type: ignore[arg-type]
        except HTTPException as exc:
            logger.warning("Could not send %s code to %s: %s", purpose, user.email, exc.detail)


@router.post("/check-email")
def check_email(body: EmailIn, db: Session = Depends(get_db)) -> dict[str, bool]:
    """Tell the sign-in page whether to ask for a password or start sign-up."""
    return {"registered": _find(db, body.email) is not None}


@router.post("/signup", response_model=UserOut, status_code=201)
def signup(body: SignupIn, response: Response, background: BackgroundTasks, db: Session = Depends(get_db)) -> UserOut:
    if _find(db, body.email):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "already_registered",
                "message": "An account with this email already exists. Sign in instead.",
            },
        )
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        name=body.name,
        phone=body.phone,
    )
    db.add(user)
    db.commit()
    start_session(db, user, response)
    background.add_task(_send_in_background, user.id, "verify")
    return UserOut.of(user)


@router.post("/login", response_model=UserOut)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> UserOut:
    email = body.email.lower()
    keys = (f"email:{email}", f"ip:{request.client.host if request.client else '?'}")
    if login_limiter.blocked(*keys):
        raise HTTPException(
            status_code=429,
            detail={
                "code": "too_many_attempts",
                "message": "Too many attempts. Please wait a few minutes and try again.",
            },
        )
    user = _find(db, email)
    if user is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_registered",
                "message": "We couldn't find an account with this email.",
            },
        )
    if not verify_password(body.password, user.password_hash):
        login_limiter.record_failure(*keys)
        raise HTTPException(
            status_code=401,
            detail={"code": "wrong_password", "message": "That password isn't right."},
        )
    login_limiter.reset(f"email:{email}")
    start_session(db, user, response)
    if user.email_verified_at is None and not has_active_code(db, user, "verify"):
        background.add_task(_send_in_background, user.id, "verify")
    return UserOut.of(user)


@router.post("/verify-email", response_model=UserOut)
def verify_email(body: CodeIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserOut:
    if user.email_verified_at is None:
        check_code(db, user, "verify", body.code)
        user.email_verified_at = utcnow()
        db.commit()
    return UserOut.of(user)


@router.post("/resend-verification")
def resend_verification(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, int]:
    if user.email_verified_at is not None:
        raise HTTPException(400, {"code": "already_verified", "message": "Your email is already confirmed."})
    issue_code(db, user, "verify")
    return {"retry_after": seconds_until_resend(db, user, "verify")}


@router.get("/verification-status")
def verification_status(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, int]:
    """Seconds until another verification code may be requested."""
    return {"retry_after": seconds_until_resend(db, user, "verify")}


@router.post("/forgot-password")
def forgot_password(body: EmailIn, db: Session = Depends(get_db)) -> dict[str, int]:
    user = _find(db, body.email)
    if user is None:
        raise HTTPException(
            404,
            {"code": "not_registered", "message": "We couldn't find an account with this email."},
        )
    issue_code(db, user, "reset")
    return {"retry_after": seconds_until_resend(db, user, "reset")}


@router.post("/reset-password", response_model=UserOut)
def reset_password(body: ResetIn, response: Response, db: Session = Depends(get_db)) -> UserOut:
    """Set a new password with an emailed code, sign out everywhere else, and sign in here."""
    user = _find(db, body.email)
    if user is None:
        raise HTTPException(400, {"code": "no_code", "message": "This code is no longer valid. Ask for a new one."})
    check_code(db, user, "reset", body.code)
    user.password_hash = hash_password(body.new_password)
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    db.commit()
    end_all_sessions(db, user)
    login_limiter.reset(f"email:{user.email}")
    start_session(db, user, response)
    return UserOut.of(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    end_session(db, request, response)
    response.status_code = 204
    return response
