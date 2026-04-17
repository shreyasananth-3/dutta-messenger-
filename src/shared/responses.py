"""Response formatting utilities for DuttaMessenger.

Provides consistent response structures for single resources,
lists with pagination, and errors.
"""

from dataclasses import dataclass, asdict
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class PaginationInfo:
    """Pagination metadata."""

    has_more: bool
    next_cursor: str | None
    limit: int


@dataclass
class SuccessResponse(Generic[T]):
    """Standard response for successful operations.

    Attributes:
        data: Response payload.
    """

    data: T


@dataclass
class PaginatedResponse(Generic[T]):
    """Standard response for paginated list operations.

    Attributes:
        data: List of items.
        pagination: Pagination metadata.
    """

    data: list[T]
    pagination: PaginationInfo


@dataclass
class ErrorDetail:
    """Error detail structure."""

    code: str
    message: str
    details: dict[str, Any] | None = None


@dataclass
class ErrorResponse:
    """Standard error response.

    Attributes:
        error: Error details.
    """

    error: ErrorDetail


def success_response(data: T) -> dict[str, Any]:
    """Format single resource response.

    Args:
        data: Resource data to return.

    Returns:
        Formatted response dictionary.

    Example:
        return success_response(user_data)
        # Returns: {"data": {...}}
    """
    return asdict(SuccessResponse(data=data))


def paginated_response(
    items: list[Any],
    has_more: bool,
    next_cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Format paginated list response.

    Args:
        items: List of items.
        has_more: Whether there are more items.
        next_cursor: Cursor for next page.
        limit: Page limit.

    Returns:
        Formatted paginated response dictionary.

    Example:
        return paginated_response(users, has_more=True, next_cursor="abc123")
        # Returns: {
        #     "data": [...],
        #     "pagination": {"has_more": true, "next_cursor": "abc123", "limit": 50}
        # }
    """
    pagination = PaginationInfo(
        has_more=has_more,
        next_cursor=next_cursor,
        limit=limit,
    )
    return asdict(PaginatedResponse(data=items, pagination=pagination))


def error_response(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format error response.

    Args:
        code: Error code.
        message: Error message.
        details: Additional error details.

    Returns:
        Formatted error response dictionary.

    Example:
        return error_response("INVALID_CREDENTIALS", "Username or password is wrong")
        # Returns: {
        #     "error": {
        #         "code": "INVALID_CREDENTIALS",
        #         "message": "Username or password is wrong",
        #         "details": null
        #     }
        # }
    """
    error = ErrorDetail(code=code, message=message, details=details or {})
    return asdict(ErrorResponse(error=error))
