"""Per-user file storage (profile photos)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from config.settings import get_settings

PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def user_dir(user_id: str) -> Path:
    """Root folder for one user's files (created on demand)."""
    if not re.fullmatch(r"[0-9a-f]{32}", user_id):
        raise ValueError("invalid user id")
    path = get_settings().user_files_dir / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def detect_image_type(head: bytes) -> str | None:
    """Return the MIME type of a JPEG, PNG or WebP image from its first bytes."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def remove_user_files(user_id: str) -> None:
    """Delete everything stored for a user."""
    shutil.rmtree(user_dir(user_id), ignore_errors=True)
