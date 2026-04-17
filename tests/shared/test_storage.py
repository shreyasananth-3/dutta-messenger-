"""Unit tests for `src/shared/storage.py`.

We mock boto3's S3 client rather than spin up moto or MinIO — at this
layer we only care that (a) the client is constructed with the right
kwargs for the active `STORAGE_TYPE`, (b) presigned URLs wire through
`generate_presigned_url` with the right params, and (c) error handling
on HEAD/DELETE matches the documented contract.

Live MinIO is exercised in integration tests for the media module
(Stage 4e), not here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.shared import storage as storage_module


@pytest.fixture(autouse=True)
def _reset_singleton() -> Any:
    """Every test gets a fresh client singleton."""
    storage_module.reset_storage_client()
    yield
    storage_module.reset_storage_client()


@pytest.fixture
def mock_client() -> MagicMock:
    """Inject a MagicMock as the S3 client for the duration of one test."""
    client = MagicMock()
    storage_module.set_storage_client(client)
    return client


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------


class TestClientConstruction:
    def test_minio_uses_endpoint_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config import settings

        monkeypatch.setattr(settings, "STORAGE_TYPE", "minio")
        monkeypatch.setattr(settings, "MINIO_URL", "http://localhost:9000")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "testkey")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "testsecret")  # noqa: S105

        seen_kwargs: dict[str, Any] = {}

        def fake_boto3_client(service: str, **kwargs: Any) -> MagicMock:
            seen_kwargs["service"] = service
            seen_kwargs.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(storage_module.boto3, "client", fake_boto3_client)

        storage_module.get_storage_client()
        assert seen_kwargs["service"] == "s3"
        assert seen_kwargs["endpoint_url"] == "http://localhost:9000"
        assert seen_kwargs["aws_access_key_id"] == "testkey"
        assert seen_kwargs["aws_secret_access_key"] == "testsecret"

    def test_s3_has_no_endpoint_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config import settings

        monkeypatch.setattr(settings, "STORAGE_TYPE", "s3")

        seen_kwargs: dict[str, Any] = {}

        def fake_boto3_client(service: str, **kwargs: Any) -> MagicMock:
            seen_kwargs["service"] = service
            seen_kwargs.update(kwargs)
            return MagicMock()

        monkeypatch.setattr(storage_module.boto3, "client", fake_boto3_client)

        storage_module.get_storage_client()
        assert seen_kwargs["service"] == "s3"
        assert "endpoint_url" not in seen_kwargs

    def test_client_is_cached(self) -> None:
        c1 = storage_module.get_storage_client()
        c2 = storage_module.get_storage_client()
        assert c1 is c2


class TestGetBucket:
    def test_minio_returns_minio_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config import settings

        monkeypatch.setattr(settings, "STORAGE_TYPE", "minio")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "dev-bucket")
        assert storage_module.get_bucket() == "dev-bucket"

    def test_s3_returns_s3_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config import settings

        monkeypatch.setattr(settings, "STORAGE_TYPE", "s3")
        monkeypatch.setattr(settings, "AWS_S3_BUCKET", "prod-bucket")
        assert storage_module.get_bucket() == "prod-bucket"


# ---------------------------------------------------------------------------
# Presigned URLs
# ---------------------------------------------------------------------------


class TestPresignedPut:
    @pytest.mark.asyncio
    async def test_builds_correct_params(self, mock_client: MagicMock) -> None:
        mock_client.generate_presigned_url.return_value = "https://signed.example/put"
        url = await storage_module.presigned_put_url(
            "media/abc/original.jpg",
            content_type="image/jpeg",
            content_length_max=5_000_000,
        )
        assert url == "https://signed.example/put"
        args, kwargs = mock_client.generate_presigned_url.call_args
        assert kwargs["ClientMethod"] == "put_object"
        assert kwargs["Params"]["Key"] == "media/abc/original.jpg"
        assert kwargs["Params"]["ContentType"] == "image/jpeg"
        assert kwargs["Params"]["ContentLength"] == 5_000_000
        assert kwargs["ExpiresIn"] == 3600  # default

    @pytest.mark.asyncio
    async def test_no_content_length_when_not_passed(
        self, mock_client: MagicMock
    ) -> None:
        mock_client.generate_presigned_url.return_value = "https://x"
        await storage_module.presigned_put_url(
            "k", content_type="image/png"
        )
        kwargs = mock_client.generate_presigned_url.call_args.kwargs
        assert "ContentLength" not in kwargs["Params"]

    @pytest.mark.asyncio
    async def test_custom_expiry(self, mock_client: MagicMock) -> None:
        mock_client.generate_presigned_url.return_value = "https://x"
        await storage_module.presigned_put_url(
            "k", content_type="image/png", expires_in=120
        )
        assert mock_client.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 120


class TestPresignedGet:
    @pytest.mark.asyncio
    async def test_builds_correct_params(self, mock_client: MagicMock) -> None:
        mock_client.generate_presigned_url.return_value = "https://signed.example/get"
        url = await storage_module.presigned_get_url("media/abc/thumb.jpg")
        assert url == "https://signed.example/get"
        kwargs = mock_client.generate_presigned_url.call_args.kwargs
        assert kwargs["ClientMethod"] == "get_object"
        assert kwargs["Params"]["Key"] == "media/abc/thumb.jpg"

    @pytest.mark.asyncio
    async def test_content_disposition_passthrough(
        self, mock_client: MagicMock
    ) -> None:
        mock_client.generate_presigned_url.return_value = "https://x"
        await storage_module.presigned_get_url(
            "k", response_content_disposition='attachment; filename="a.jpg"'
        )
        params = mock_client.generate_presigned_url.call_args.kwargs["Params"]
        assert params["ResponseContentDisposition"] == 'attachment; filename="a.jpg"'


# ---------------------------------------------------------------------------
# HEAD + exists
# ---------------------------------------------------------------------------


def _make_client_error(code: str) -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": "X"}},
        operation_name="HeadObject",
    )


class TestHeadObject:
    @pytest.mark.asyncio
    async def test_happy_returns_metadata(self, mock_client: MagicMock) -> None:
        mock_client.head_object.return_value = {
            "ContentLength": 1234,
            "ContentType": "image/jpeg",
            "ETag": '"abc"',
        }
        meta = await storage_module.head_object("media/x.jpg")
        assert meta is not None
        assert meta["ContentLength"] == 1234

    @pytest.mark.asyncio
    async def test_missing_returns_none_404(self, mock_client: MagicMock) -> None:
        mock_client.head_object.side_effect = _make_client_error("404")
        meta = await storage_module.head_object("missing.jpg")
        assert meta is None

    @pytest.mark.asyncio
    async def test_missing_returns_none_nosuchkey(
        self, mock_client: MagicMock
    ) -> None:
        mock_client.head_object.side_effect = _make_client_error("NoSuchKey")
        meta = await storage_module.head_object("missing.jpg")
        assert meta is None

    @pytest.mark.asyncio
    async def test_other_errors_propagate(self, mock_client: MagicMock) -> None:
        mock_client.head_object.side_effect = _make_client_error("AccessDenied")
        with pytest.raises(ClientError):
            await storage_module.head_object("forbidden.jpg")


class TestObjectExists:
    @pytest.mark.asyncio
    async def test_true_when_head_succeeds(self, mock_client: MagicMock) -> None:
        mock_client.head_object.return_value = {"ContentLength": 10}
        assert await storage_module.object_exists("x") is True

    @pytest.mark.asyncio
    async def test_false_when_head_404(self, mock_client: MagicMock) -> None:
        mock_client.head_object.side_effect = _make_client_error("404")
        assert await storage_module.object_exists("missing") is False


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


class TestDeleteObject:
    @pytest.mark.asyncio
    async def test_calls_delete(self, mock_client: MagicMock) -> None:
        await storage_module.delete_object("media/x.jpg")
        mock_client.delete_object.assert_called_once()
        kwargs = mock_client.delete_object.call_args.kwargs
        assert kwargs["Key"] == "media/x.jpg"

    @pytest.mark.asyncio
    async def test_custom_bucket(self, mock_client: MagicMock) -> None:
        await storage_module.delete_object("k", bucket="other")
        assert mock_client.delete_object.call_args.kwargs["Bucket"] == "other"
