"""Unit tests for AppException hierarchy."""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from src.shared.exceptions import (
    AppException,
    AuthenticationError,
    ConflictError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ValidationError,
)


class TestAppException:
    def test_minimal_construction(self) -> None:
        exc = AppException(error_code="X", message="bad")
        assert exc.error_code == "X"
        assert exc.message == "bad"
        assert exc.status_code == status.HTTP_400_BAD_REQUEST
        assert exc.details == {}
        assert str(exc) == "bad"

    def test_details_default_to_empty_dict(self) -> None:
        exc = AppException("X", "bad", details=None)
        assert exc.details == {}

    def test_to_http_exception_envelope(self) -> None:
        exc = AppException("X", "bad", status_code=418, details={"a": 1})
        http = exc.to_http_exception()
        assert isinstance(http, HTTPException)
        assert http.status_code == 418
        assert http.detail == {
            "error": {"code": "X", "message": "bad", "details": {"a": 1}}
        }


class TestNotFoundError:
    def test_status_and_envelope(self) -> None:
        exc = NotFoundError("user", "u-1")
        assert exc.status_code == 404
        assert exc.error_code == "NOT_FOUND"
        assert exc.details == {"resource_type": "user", "resource_id": "u-1"}
        assert "user" in exc.message


class TestPermissionDeniedError:
    def test_default_message(self) -> None:
        exc = PermissionDeniedError()
        assert exc.status_code == 403
        assert exc.error_code == "PERMISSION_DENIED"
        assert exc.message == "Permission denied"

    def test_custom_message(self) -> None:
        assert PermissionDeniedError("nope").message == "nope"


class TestAuthenticationError:
    def test_default(self) -> None:
        exc = AuthenticationError()
        assert exc.status_code == 401
        assert exc.error_code == "AUTHENTICATION_FAILED"


class TestValidationError:
    def test_no_field(self) -> None:
        exc = ValidationError("bad")
        assert exc.status_code == 422
        assert exc.details == {}

    def test_with_field(self) -> None:
        exc = ValidationError("bad", field="email")
        assert exc.details == {"field": "email"}


class TestConflictError:
    def test_no_resource(self) -> None:
        exc = ConflictError("dup")
        assert exc.status_code == 409
        assert exc.details == {}

    def test_with_resource(self) -> None:
        assert ConflictError("dup", resource_type="user").details == {
            "resource_type": "user"
        }


class TestRateLimitError:
    def test_defaults(self) -> None:
        exc = RateLimitError()
        assert exc.status_code == 429
        assert exc.error_code == "RATE_LIMIT_EXCEEDED"


class TestInternalServerError:
    def test_defaults(self) -> None:
        exc = InternalServerError()
        assert exc.status_code == 500
        assert exc.error_code == "INTERNAL_SERVER_ERROR"

    def test_can_be_raised_and_caught_as_app_exception(self) -> None:
        with pytest.raises(AppException) as info:
            raise InternalServerError("boom")
        assert info.value.message == "boom"
