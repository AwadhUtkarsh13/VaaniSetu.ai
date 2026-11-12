"""The signed-in user's profile, photo and password."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from config.settings import get_settings
from server.db import User, get_db, utcnow
from server.schemas import PasswordChange, ProfileUpdate, UserOut
from server.security import (
    current_user,
    end_all_sessions,
    hash_password,
    verified_user,
    verify_password,
)
from server.storage import PHOTO_TYPES, detect_image_type, user_dir

router = APIRouter(prefix="/api/me", tags=["profile"])


@router.get("", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.of(user)


@router.patch("", response_model=UserOut)
def update_profile(body: ProfileUpdate, user: User = Depends(verified_user), db: Session = Depends(get_db)) -> UserOut:
    """Update any of name, phone, date of birth and gender (omitted fields stay as they are)."""
    user = db.merge(user)
    for field in body.model_fields_set:
        setattr(user, field, getattr(body, field))
    user.updated_at = utcnow()
    db.commit()
    return UserOut.of(user)


@router.post("/photo", response_model=UserOut)
def upload_photo(file: UploadFile, user: User = Depends(verified_user), db: Session = Depends(get_db)) -> UserOut:
    limit = get_settings().max_photo_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            413,
            {
                "code": "too_large",
                "message": f"Photos can be up to {get_settings().max_photo_mb} MB.",
            },
        )
    mime = detect_image_type(data[:16])
    if mime is None:
        raise HTTPException(415, {"code": "bad_type", "message": "Please choose a JPG, PNG or WebP image."})
    folder = user_dir(user.id)
    for old in folder.glob("photo.*"):
        old.unlink(missing_ok=True)
    name = "photo" + PHOTO_TYPES[mime]
    (folder / name).write_bytes(data)
    user = db.merge(user)
    user.photo_filename = name
    user.updated_at = utcnow()
    db.commit()
    return UserOut.of(user)


@router.get("/photo")
def get_photo(user: User = Depends(verified_user)) -> Response:
    if not user.photo_filename:
        raise HTTPException(404, {"code": "no_photo", "message": "No photo yet."})
    path = user_dir(user.id) / user.photo_filename
    if not path.exists():
        raise HTTPException(404, {"code": "no_photo", "message": "No photo yet."})
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/photo", response_model=UserOut)
def delete_photo(user: User = Depends(verified_user), db: Session = Depends(get_db)) -> UserOut:
    for old in user_dir(user.id).glob("photo.*"):
        old.unlink(missing_ok=True)
    user = db.merge(user)
    user.photo_filename = None
    user.updated_at = utcnow()
    db.commit()
    return UserOut.of(user)


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChange,
    request: Request,
    user: User = Depends(verified_user),
    db: Session = Depends(get_db),
) -> Response:
    """Change the password and sign out every other device."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, {"code": "wrong_password", "message": "Your current password isn't right."})
    user = db.merge(user)
    user.password_hash = hash_password(body.new_password)
    user.updated_at = utcnow()
    db.commit()
    end_all_sessions(db, user, keep_request=request)
    return Response(status_code=204)
