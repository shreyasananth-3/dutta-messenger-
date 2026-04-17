"""Cursor-based pagination utilities for DuttaMessenger.

Provides encoding/decoding of cursor-based pagination tokens
following API_STANDARDS.md specifications.
"""

import base64
import json
from typing import Any


def encode_cursor(data: dict[str, Any]) -> str:
    """Encode cursor data to base64 string.

    Args:
        data: Dictionary containing cursor information (e.g., {'id': 'uuid', 'timestamp': '2024-01-01'}).

    Returns:
        Base64-encoded cursor string.

    Example:
        cursor = encode_cursor({'id': 'abc123', 'timestamp': '2024-01-01'})
        # Returns base64-encoded string
    """
    json_str = json.dumps(data)
    return base64.b64encode(json_str.encode()).decode()


def decode_cursor(cursor: str) -> dict[str, Any] | None:
    """Decode cursor from base64 string.

    Args:
        cursor: Base64-encoded cursor string.

    Returns:
        Decoded dictionary or None if invalid.

    Example:
        data = decode_cursor(cursor)
        # Returns {'id': 'abc123', 'timestamp': '2024-01-01'}
    """
    try:
        json_str = base64.b64decode(cursor.encode()).decode()
        return json.loads(json_str)
    except (ValueError, json.JSONDecodeError):
        return None


class PaginationParams:
    """Helper class for pagination parameters."""

    def __init__(self, limit: int = 50, cursor: str | None = None) -> None:
        """Initialize pagination parameters.

        Args:
            limit: Number of items per page (default 50, max 100).
            cursor: Cursor for pagination (base64-encoded).
        """
        self.limit = min(limit, 100)
        self.cursor = cursor
        self.cursor_data = decode_cursor(cursor) if cursor else None

    def get_limit(self) -> int:
        """Get page limit."""
        return self.limit

    def get_cursor_id(self) -> str | None:
        """Get ID from cursor."""
        if self.cursor_data:
            return self.cursor_data.get("id")
        return None

    def get_cursor_timestamp(self) -> str | None:
        """Get timestamp from cursor."""
        if self.cursor_data:
            return self.cursor_data.get("timestamp")
        return None


def build_next_cursor(items: list[Any], item_id_field: str = "id") -> str | None:
    """Build next cursor from last item in list.

    Args:
        items: List of items.
        item_id_field: Field name containing the ID.

    Returns:
        Encoded cursor for next page, or None if no items.

    Example:
        items = [{'id': 'abc', 'name': 'foo'}, {'id': 'def', 'name': 'bar'}]
        cursor = build_next_cursor(items)
    """
    if not items:
        return None

    last_item = items[-1]
    item_id = getattr(last_item, item_id_field, None)

    if not item_id:
        return None

    return encode_cursor({"id": str(item_id)})
