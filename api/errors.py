from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_STATUS_CODE_FALLBACK: dict[int, str] = {
    400: "BAD_REQUEST",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    500: "INTERNAL_ERROR",
}


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
        body = exc.detail
    else:
        body = {
            "code": _STATUS_CODE_FALLBACK.get(exc.status_code, "ERROR"),
            "message": str(exc.detail),
        }
    return JSONResponse(status_code=exc.status_code, content={"error": body}, headers=exc.headers)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    messages = "; ".join(
        f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
    )
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": messages}},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method, request.url.path, exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}},
    )
