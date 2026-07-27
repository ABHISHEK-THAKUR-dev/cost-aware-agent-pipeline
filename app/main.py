import logging

from fastapi import Depends, FastAPI, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.errors import register_exception_handlers
from app.logging_conf import configure_logging
from app.pipeline import run_pipeline
from app.schemas import HealthResponse, QueryRequest, QueryResponse, UploadResponse
from app.security import (
    enforce_rate_limit,
    new_trace_id,
    query_limiter,
    require_api_key,
    upload_limiter,
    validate_and_store_upload,
)

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("main")

app = FastAPI(title="Cost-Aware Agent Pipeline", version="0.1.0")
register_exception_handlers(app)

# CORS left restrictive by default — widen only for known frontend origins in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    # Used by the deploy pipeline's smoke test and by the rollback decision (docs/rule.md).
    return HealthResponse(status="ok", environment=settings.environment)


@app.post("/v1/query", response_model=QueryResponse)
async def query(body: QueryRequest, api_key: str = Depends(require_api_key)) -> QueryResponse:
    enforce_rate_limit(api_key, query_limiter, settings.rate_limit_query_per_min)
    trace_id = new_trace_id()
    logger.info("query_received", extra={"extra_fields": {"trace_id": trace_id, "api_key": api_key}})
    result = run_pipeline(trace_id, body.query, body.context_documents)
    return QueryResponse(trace_id=trace_id, **result)


@app.post("/v1/upload", response_model=UploadResponse)
async def upload(
    file: UploadFile, api_key: str = Depends(require_api_key)
) -> UploadResponse:
    enforce_rate_limit(api_key, upload_limiter, settings.rate_limit_upload_per_min)
    trace_id = new_trace_id()
    stored_name, size = await validate_and_store_upload(file, upload_dir=settings.upload_dir)
    logger.info(
        "upload_stored",
        extra={"extra_fields": {"trace_id": trace_id, "stored_name": stored_name, "size": size}},
    )
    return UploadResponse(
        trace_id=trace_id,
        stored_name=stored_name,
        original_filename=file.filename or "unnamed",
        size_bytes=size,
        status="stored",
    )
