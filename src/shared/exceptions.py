"""Custom exception classes for DuttaMessenger.

Provides application-specific exceptions with structured error codes
and detailed information for API error responses.
"""

from fastapi import HTTPException, status


class AppException(Exception):
    """Base exception for all application-specific errors."""

    def __init__(
        self,
        error_code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict | None = None,
    ) -> None:
        """Initialize app exception.

        Args:
            error_code: Machine-readable error code.
            message: Human-readable error message.
            status_code: HTTP status code.
            details: Additional context about the error.
        """
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)

    def to_http_exception(self) -> HTTPException:
        """Convert to FastAPI HTTPException."""
        return HTTPException(
            status_code=self.status_code,
            detail={
                "error": {
                    "code": self.error_code,
                    "message": self.message,
                    "details": self.details,
                }
            },
        )


class NotFoundError(AppException):
    """Resource not found exception."""

    def __init__(self, resource_type: str, resource_id: str) -> None:
        """Initialize not found error.

        Args:
            resource_type: Type of resource (e.g., 'user', 'group').
            resource_id: ID of the missing resource.
        """
        super().__init__(
            error_code="NOT_FOUND",
            message=f"{resource_type} not found",
            status_code=status.HTTP_404_NOT_FOUND,
            details={"resource_type": resource_type, "resource_id": resource_id},
        )


class PermissionDeniedError(AppException):
    """Permission denied exception."""

    def __init__(self, message: str = "Permission denied") -> None:
        """Initialize permission denied error.

        Args:
            message: Detailed error message.
        """
        super().__init__(
            error_code="PERMISSION_DENIED",
            message=message,
            status_code=status.HTTP_403_FORBIDDEN,
        )


class AuthenticationError(AppException):
    """Authentication failure exception."""

    def __init__(self, message: str = "Authentication failed") -> None:
        """Initialize authentication error.

        Args:
            message: Detailed error message.
        """
        super().__init__(
            error_code="AUTHENTICATION_FAILED",
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class ValidationError(AppException):
    """Data validation exception."""

    def __init__(self, message: str, field: str | None = None) -> None:
        """Initialize validation error.

        Args:
            message: Detailed error message.
            field: Field that failed validation.
        """
        details = {}
        if field:
            details["field"] = field
        super().__init__(
            error_code="VALIDATION_ERROR",
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=details,
        )


class ConflictError(AppException):
    """Resource conflict exception."""

    def __init__(self, message: str, resource_type: str | None = None) -> None:
        """Initialize conflict error.

        Args:
            message: Detailed error message.
            resource_type: Type of resource in conflict.
        """
        details = {}
        if resource_type:
            details["resource_type"] = resource_type
        super().__init__(
            error_code="CONFLICT",
            message=message,
            status_code=status.HTTP_409_CONFLICT,
            details=details,
        )


class RateLimitError(AppException):
    """Rate limit exceeded exception."""

    def __init__(self, message: str = "Rate limit exceeded") -> None:
        """Initialize rate limit error.

        Args:
            message: Detailed error message.
        """
        super().__init__(
            error_code="RATE_LIMIT_EXCEEDED",
            message=message,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )


class InternalServerError(AppException):
    """Internal server error exception."""

    def __init__(self, message: str = "Internal server error") -> None:
        """Initialize internal server error.

        Args:
            message: Detailed error message.
        """
        super().__init__(
            error_code="INTERNAL_SERVER_ERROR",
            message=message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
