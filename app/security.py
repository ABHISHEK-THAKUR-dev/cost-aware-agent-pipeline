"""
Rate limiting, API-key auth, and file-upload safety.
Maps directly to docs/rule.md Security Rules > Rate limiting / File upload safety.
"""
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import Header, HTTPException, UploadFile, status

from app.config import get_settings


class TokenBucketLimiter:
    """
    Simple in-process sliding-window limiter keyed by API key.
    NOTE: in-process only — fine for a single instance/demo. docs/phases.md Phase 2 calls
    out moving this to Redis before running >1 instance.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit_per_min: int) -> None:
        now = time.time()
        window_start = now - 60
        hits = self._hits[key]
        while hits and hits[0] < window_start:
            hits.popleft()
        if len(hits) >= limit_per_min:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again shortly.",
                headers={"Retry-After": "60"},
            )
        hits.append(now)


query_limiter = TokenBucketLimiter()
upload_limiter = TokenBucketLimiter()


def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    settings = get_settings()
    allowed = settings.allowed_api_keys
    # If no keys are configured (e.g. local dev), auth is a no-op — explicit, not accidental.
    if not allowed:
        return "anonymous-dev"
    if not x_api_key or x_api_key not in allowed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return x_api_key


def enforce_rate_limit(api_key: str, limiter: TokenBucketLimiter, limit_per_min: int) -> None:
    limiter.check(api_key, limit_per_min)


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


async def validate_and_store_upload(file: UploadFile, upload_dir: str) -> tuple[str, int]:
    """
    Enforces docs/rule.md File upload safety rules:
    - extension allow-list
    - size cap enforced during read, not after
    - server-generated storage filename (original filename never used as a path)
    - no execution/deserialization of content
    """
    settings = get_settings()
    original = file.filename or "unnamed"
    ext = os.path.splitext(original)[1].lower()

    if ext not in settings.allowed_upload_ext_set:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File type '{ext}' not allowed.",
        )

    os.makedirs(upload_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = os.path.join(upload_dir, stored_name)

    size = 0
    chunk_size = 1024 * 1024
    with open(stored_path, "wb") as out:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                os.remove(stored_path)
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="File exceeds maximum allowed size.",
                )
            out.write(chunk)

    return stored_name, size
