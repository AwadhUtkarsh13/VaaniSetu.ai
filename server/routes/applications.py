"""Finished eligibility checks and prepared grievances (validated, PII-redacted payloads)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from server.db import Application, User, get_db
from server.schemas import ApplicationDetail, ApplicationOut
from server.security import verified_user

router = APIRouter(prefix="/api/applications", tags=["applications"])


def _owned(db: Session, user: User, application_id: str) -> Application:
    app = db.get(Application, application_id)
    if app is None or app.user_id != user.id:
        raise HTTPException(404, {"code": "not_found", "message": "That application no longer exists."})
    return app


@router.get("", response_model=list[ApplicationOut])
def list_applications(user: User = Depends(verified_user), db: Session = Depends(get_db)) -> list[ApplicationOut]:
    apps = (
        db.query(Application)
        .filter(Application.user_id == user.id)
        .order_by(Application.created_at.desc())
        .limit(200)
        .all()
    )
    return [ApplicationOut.of(a) for a in apps]


@router.get("/{application_id}", response_model=ApplicationDetail)
def get_application(
    application_id: str, user: User = Depends(verified_user), db: Session = Depends(get_db)
) -> ApplicationDetail:
    return ApplicationDetail.of(_owned(db, user, application_id))


@router.delete("/{application_id}", status_code=204)
def delete_application(
    application_id: str, user: User = Depends(verified_user), db: Session = Depends(get_db)
) -> Response:
    db.delete(_owned(db, user, application_id))
    db.commit()
    return Response(status_code=204)
