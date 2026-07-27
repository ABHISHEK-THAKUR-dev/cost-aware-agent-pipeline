"""
Central exception -> HTTP response mapping.
Rule: stack traces, prompts, and upstream (NIM) raw errors never reach the client.
Full detail goes to the structured logger, keyed by trace_id. See docs/rule.md
Security Rules > Error handling / info leakage.
"""
import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("errors")


class PipelineError(Exception):
    """Raised by pipeline steps on unrecoverable failure (timeout, malformed output, etc)."""

    def __init__(self, message: str, step: str, trace_id: str):
        super().__init__(message)
        self.message = message
        self.step = step
        self.trace_id = trace_id


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # Validation errors ARE safe to detail — they describe the client's own input.
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_request", "detail": exc.errors()},
        )

    @app.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(PipelineError)
    async def pipeline_error_handler(request: Request, exc: PipelineError):
        logger.error(
            "pipeline_error",
            extra={
                "extra_fields": {
                    "trace_id": exc.trace_id,
                    "step": exc.step,
                    "error": exc.message,
                }
            },
        )
        return JSONResponse(
            status_code=502,
            content={
                "error": "pipeline_failed",
                "trace_id": exc.trace_id,
                "detail": "The pipeline could not complete this request. "
                "Provide this trace_id to support/operators.",
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        trace_id = uuid.uuid4().hex[:16]
        logger.exception(
            "unhandled_exception",
            extra={"extra_fields": {"trace_id": trace_id}},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "trace_id": trace_id,
                "detail": "An unexpected error occurred. Provide this trace_id to operators.",
            },
        )
