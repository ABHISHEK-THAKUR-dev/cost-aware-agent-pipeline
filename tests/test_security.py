import io

import pytest
from fastapi import HTTPException, UploadFile

from app.security import TokenBucketLimiter, validate_and_store_upload


def test_rate_limiter_allows_under_limit():
    limiter = TokenBucketLimiter()
    for _ in range(5):
        limiter.check("client-a", limit_per_min=5)  # should not raise


def test_rate_limiter_blocks_over_limit():
    limiter = TokenBucketLimiter()
    for _ in range(5):
        limiter.check("client-b", limit_per_min=5)
    with pytest.raises(HTTPException) as exc_info:
        limiter.check("client-b", limit_per_min=5)
    assert exc_info.value.status_code == 429


def test_rate_limiter_keys_are_independent():
    limiter = TokenBucketLimiter()
    for _ in range(5):
        limiter.check("client-c", limit_per_min=5)
    limiter.check("client-d", limit_per_min=5)  # different key, should not raise


@pytest.mark.asyncio
async def test_upload_rejects_disallowed_extension(tmp_path):
    fake_file = UploadFile(filename="malware.exe", file=io.BytesIO(b"fake content"))
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_store_upload(fake_file, upload_dir=str(tmp_path))
    assert exc_info.value.status_code == 415


@pytest.mark.asyncio
async def test_upload_accepts_allowed_extension_and_uses_generated_name(tmp_path):
    fake_file = UploadFile(filename="../../etc/passwd.txt", file=io.BytesIO(b"hello"))
    stored_name, size = await validate_and_store_upload(fake_file, upload_dir=str(tmp_path))
    # Original (path-traversal-shaped) filename must never be used as the stored name.
    assert ".." not in stored_name
    assert "/" not in stored_name
    assert size == 5


@pytest.mark.asyncio
async def test_upload_enforces_size_cap(monkeypatch, tmp_path):
    from app import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "10")  # tiny cap for the test
    big_file = UploadFile(filename="big.txt", file=io.BytesIO(b"x" * 1000))
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_store_upload(big_file, upload_dir=str(tmp_path))
    assert exc_info.value.status_code == 413
    config.get_settings.cache_clear()
