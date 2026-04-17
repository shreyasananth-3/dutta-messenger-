"""Unit tests for response envelope helpers."""

from __future__ import annotations

from src.shared.responses import (
    error_response,
    paginated_response,
    success_response,
)


class TestSuccessResponse:
    def test_wraps_data(self) -> None:
        assert success_response({"id": 1}) == {"data": {"id": 1}}

    def test_supports_primitive(self) -> None:
        assert success_response("ok") == {"data": "ok"}

    def test_supports_list(self) -> None:
        assert success_response([1, 2, 3]) == {"data": [1, 2, 3]}


class TestPaginatedResponse:
    def test_envelope_structure(self) -> None:
        result = paginated_response(
            items=[{"id": "a"}, {"id": "b"}],
            has_more=True,
            next_cursor="cursor-token",
            limit=25,
        )
        assert result == {
            "data": [{"id": "a"}, {"id": "b"}],
            "pagination": {
                "has_more": True,
                "next_cursor": "cursor-token",
                "limit": 25,
            },
        }

    def test_defaults_when_no_more_pages(self) -> None:
        result = paginated_response(items=[], has_more=False)
        assert result["pagination"]["next_cursor"] is None
        assert result["pagination"]["limit"] == 50
        assert result["pagination"]["has_more"] is False
        assert result["data"] == []


class TestErrorResponse:
    def test_minimal(self) -> None:
        assert error_response("E", "boom") == {
            "error": {"code": "E", "message": "boom", "details": {}}
        }

    def test_with_details(self) -> None:
        assert error_response("E", "boom", {"field": "x"}) == {
            "error": {"code": "E", "message": "boom", "details": {"field": "x"}}
        }
