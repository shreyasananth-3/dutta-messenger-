"""Unit tests for cursor pagination utilities."""

from __future__ import annotations

import base64
from dataclasses import dataclass

import pytest

from src.shared.utils.pagination import (
    PaginationParams,
    build_next_cursor,
    decode_cursor,
    encode_cursor,
)


@dataclass
class _Item:
    id: str


class TestEncodeDecodeCursor:
    def test_roundtrip_preserves_payload(self) -> None:
        payload = {"id": "abc-123", "timestamp": "2026-04-18T00:00:00+00:00"}
        cursor = encode_cursor(payload)
        assert decode_cursor(cursor) == payload

    def test_encoded_cursor_is_url_safe_base64(self) -> None:
        cursor = encode_cursor({"id": "abc"})
        # Round-trip via base64 to confirm it actually decodes
        base64.b64decode(cursor.encode())

    def test_decode_invalid_base64_returns_none(self) -> None:
        assert decode_cursor("!!!not-base64!!!") is None

    def test_decode_non_json_returns_none(self) -> None:
        garbage = base64.b64encode(b"not json at all").decode()
        assert decode_cursor(garbage) is None

    def test_decode_empty_string_returns_none(self) -> None:
        assert decode_cursor("") is None


class TestPaginationParams:
    def test_default_limit_is_50(self) -> None:
        params = PaginationParams()
        assert params.get_limit() == 50
        assert params.get_cursor_id() is None
        assert params.get_cursor_timestamp() is None

    def test_limit_is_capped_at_100(self) -> None:
        assert PaginationParams(limit=999).get_limit() == 100

    def test_limit_respects_explicit_value(self) -> None:
        assert PaginationParams(limit=25).get_limit() == 25

    def test_cursor_data_extracted(self) -> None:
        cursor = encode_cursor({"id": "u-1", "timestamp": "2026-01-01"})
        params = PaginationParams(limit=10, cursor=cursor)
        assert params.get_cursor_id() == "u-1"
        assert params.get_cursor_timestamp() == "2026-01-01"

    def test_invalid_cursor_falls_back_to_none(self) -> None:
        params = PaginationParams(cursor="!!!bad!!!")
        assert params.get_cursor_id() is None
        assert params.get_cursor_timestamp() is None


class TestBuildNextCursor:
    def test_empty_list_returns_none(self) -> None:
        assert build_next_cursor([]) is None

    def test_uses_last_item_id(self) -> None:
        items = [_Item("a"), _Item("b"), _Item("c")]
        cursor = build_next_cursor(items)
        assert cursor is not None
        decoded = decode_cursor(cursor)
        assert decoded == {"id": "c"}

    def test_missing_id_field_returns_none(self) -> None:
        @dataclass
        class _NoId:
            name: str

        assert build_next_cursor([_NoId("x")]) is None

    def test_unicode_id_roundtrips(self) -> None:
        cursor = build_next_cursor([_Item("नमस्ते-😀")])
        assert decode_cursor(cursor) == {"id": "नमस्ते-😀"}
