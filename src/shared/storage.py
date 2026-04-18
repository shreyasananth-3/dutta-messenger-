"""Object-storage abstraction over S3 / MinIO.

DuttaMessenger's media module writes uploads to MinIO in dev and S3 (or any
S3-compatible store) in prod. This module is the thin wrapper every caller
uses so we don't leak `boto3.Session` details into service code.

Operations exposed:
  - `presigned_put_url(...)`  → URL the client PUTs the file to directly.
  - `presigned_get_url(...)`  → URL the client GETs the file from.
  - `head_object(...)`        → verify an upload finished (content-length, ETag).
  - `delete_object(...)`      → permanent purge (e.g. recycle-bin sweep).
  - `object_exists(...)`      → true iff a HEAD succeeds.

All operations are async. Under the hood boto3 is synchronous; we run each
blocking call inside FastAPI's `run_in_threadpool` so the event loop stays
responsive. At the 1-5k-user scale described in the plan this is plenty
fast (blob ops happen off the request hot path anyway — clients POST to
presigned URLs directly, not through our app).

The choice between S3 and MinIO is driven by `settings.STORAGE_TYPE`:
  - "minio"  → uses `MINIO_URL` as the endpoint, `path` addressing.
  - "s3"     → uses AWS regional endpoint (endpoint_url=None), virtual addressing.

Tests: `tests/shared/test_storage.py` covers the client-construction and
signature-URL logic with moto's in-memory S3. Live S3/MinIO is exercised
only in integration tests for the media module (Stage 4e).
"""

from __future__ import annotations

from typing import Any, cast

import boto3
import structlog
from botocore.client import Config as _BotoConfig
from botocore.exceptions import ClientError
from starlette.concurrency import run_in_threadpool

from src.config import settings

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Client construction — singleton for process lifetime. boto3 clients are
# thread-safe, so one shared instance is fine across request handlers.
# ---------------------------------------------------------------------------

_client: Any = None


def get_storage_client() -> Any:
    """Return the shared boto3 S3 client, building it on first call.

    Kept as a function (not a module-level constant) so tests can swap the
    client in via `set_storage_client(mock)` without import-time side effects.
    """
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def set_storage_client(client: Any) -> None:
    """Test-only hook: override the singleton for the duration of a test."""
    global _client
    _client = client


def reset_storage_client() -> None:
    """Test-only hook: clear the singleton so the next call rebuilds."""
    global _client
    _client = None


def _build_client() -> Any:
    storage_type = settings.STORAGE_TYPE.lower()
    if storage_type == "minio":
        return boto3.client(
            "s3",
            endpoint_url=settings.MINIO_URL,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            # MinIO supports v4 signatures; stick with the default but pin
            # path-style addressing (virtual-hosted doesn't work with
            # http://minio.local:9000).
            config=_BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    # Default: AWS S3 (or any S3 API with default endpoint)
    return boto3.client(
        "s3",
        config=_BotoConfig(signature_version="s3v4"),
    )


def get_bucket() -> str:
    """Return the configured bucket for the active storage type."""
    return (
        settings.MINIO_BUCKET
        if settings.STORAGE_TYPE.lower() == "minio"
        else settings.AWS_S3_BUCKET
    )


# ---------------------------------------------------------------------------
# High-level async API (what service code calls)
# ---------------------------------------------------------------------------


async def presigned_put_url(
    key: str,
    *,
    content_type: str,
    content_length_max: int | None = None,
    expires_in: int = 3600,
    bucket: str | None = None,
) -> str:
    """Return a presigned URL the client uses to `PUT` the file directly.

    Args:
        key: Object key (e.g. `media/{media_id}/original.jpg`).
        content_type: Expected `Content-Type` the client will send. Locked
            in the signature so the client can't silently change it.
        content_length_max: If set, signature restricts the upload size.
        expires_in: URL validity in seconds. Default 1h.
        bucket: Override the default bucket (rarely needed).
    """
    params: dict[str, Any] = {
        "Bucket": bucket or get_bucket(),
        "Key": key,
        "ContentType": content_type,
    }
    if content_length_max is not None:
        params["ContentLength"] = content_length_max

    def _sign() -> str:
        url = get_storage_client().generate_presigned_url(
            ClientMethod="put_object",
            Params=params,
            ExpiresIn=expires_in,
        )
        return cast(str, url)

    return await run_in_threadpool(_sign)


async def presigned_get_url(
    key: str,
    *,
    expires_in: int = 3600,
    bucket: str | None = None,
    response_content_disposition: str | None = None,
) -> str:
    """Return a presigned URL the client uses to `GET` the file directly."""
    params: dict[str, Any] = {
        "Bucket": bucket or get_bucket(),
        "Key": key,
    }
    if response_content_disposition is not None:
        params["ResponseContentDisposition"] = response_content_disposition

    def _sign() -> str:
        url = get_storage_client().generate_presigned_url(
            ClientMethod="get_object",
            Params=params,
            ExpiresIn=expires_in,
        )
        return cast(str, url)

    return await run_in_threadpool(_sign)


async def head_object(
    key: str,
    *,
    bucket: str | None = None,
) -> dict[str, Any] | None:
    """Return metadata for `key`, or `None` if the object doesn't exist.

    Used after an upload to confirm the client actually PUT the bytes
    before we flip a `media_files.status` row from `pending` to `ready`.
    The return shape matches boto3's `head_object` response (ContentLength,
    ContentType, ETag, LastModified, Metadata, …).
    """
    target_bucket = bucket or get_bucket()

    def _head() -> dict[str, Any] | None:
        try:
            result = get_storage_client().head_object(Bucket=target_bucket, Key=key)
            return cast("dict[str, Any]", result)
        except ClientError as exc:
            # 404 on a HEAD is expected for pending uploads.
            err_code = exc.response.get("Error", {}).get("Code")
            if err_code in ("404", "NoSuchKey", "NotFound"):
                return None
            logger.error(
                "storage_head_failed",
                bucket=target_bucket,
                key=key,
                error_code=err_code,
            )
            raise

    return await run_in_threadpool(_head)


async def object_exists(
    key: str,
    *,
    bucket: str | None = None,
) -> bool:
    """Boolean convenience over `head_object`."""
    meta = await head_object(key, bucket=bucket)
    return meta is not None


async def delete_object(
    key: str,
    *,
    bucket: str | None = None,
) -> None:
    """Permanently delete `key`. Idempotent — no error if the key is gone.

    Called from the recycle-bin sweep Celery task
    (`docs/design/privacy-erasure.md` §"Media recycle bin").
    """
    target_bucket = bucket or get_bucket()

    def _delete() -> None:
        get_storage_client().delete_object(Bucket=target_bucket, Key=key)

    await run_in_threadpool(_delete)


__all__ = [
    "delete_object",
    "get_bucket",
    "get_storage_client",
    "head_object",
    "object_exists",
    "presigned_get_url",
    "presigned_put_url",
    "reset_storage_client",
    "set_storage_client",
]
