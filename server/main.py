"""FastAPI application: JSON API under ``/api`` plus the built React app."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config.settings import get_settings
from server.chat_service import engine
from server.db import init_db
from server.routes import applications, auth, chat, conversations, profile

logger = logging.getLogger(__name__)

_FIELD_MESSAGES = {"email": "Enter a valid email address."}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    init_db()
    path = get_settings().checkpoint_path
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as checkpointer:
        warm_up = asyncio.create_task(engine.start(checkpointer))
        try:
            yield
        finally:
            warm_up.cancel()
            await engine.stop()


def create_app(*, with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(
        title="VaaniSetu AI",
        lifespan=lifespan if with_lifespan else None,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "microphone=(self)")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields: dict[str, str] = {}
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path", "form")]
            field = loc[0] if loc else "request"
            message = _FIELD_MESSAGES.get(field) or str(err.get("msg", "Invalid value")).removeprefix("Value error, ")
            fields.setdefault(field, message)
        first = next(iter(fields.values()), "Please check the form.")
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "invalid", "message": first, "fields": fields}},
        )

    for router in (
        auth.router,
        profile.router,
        conversations.router,
        chat.router,
        applications.router,
    ):
        app.include_router(router)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        from server.mailer import delivery_mode

        pipeline = engine.pipeline
        return {
            "ready": engine.ready,
            "error": engine.error,
            "email": delivery_mode(),
            "voice": pipeline.voice_provider if pipeline else None,
        }

    @app.api_route(
        "/api/{_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def api_not_found(_path: str) -> None:
        raise HTTPException(404, {"code": "not_found", "message": "Not found."})

    dist = get_settings().frontend_dist
    if (dist / "index.html").exists():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
