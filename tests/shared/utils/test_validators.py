"""Unit tests for shared input validators."""

from __future__ import annotations

import pytest

from src.shared.exceptions import ValidationError
from src.shared.utils.validators import (
    validate_email,
    validate_file_size,
    validate_file_type,
    validate_full_name,
    validate_message_content,
    validate_password,
    validate_phone_number,
    validate_url_slug,
)


class TestValidateEmail:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("user@example.com", "user@example.com"),
            ("  USER@Example.COM  ", "user@example.com"),
            ("first.last+tag@sub.example.co.in", "first.last+tag@sub.example.co.in"),
        ],
    )
    def test_valid_email_normalised(self, raw: str, expected: str) -> None:
        assert validate_email(raw) == expected

    @pytest.mark.parametrize(
        "bad", ["", "  ", "no-at-sign", "missing@tld", "@example.com", "user@.com"]
    )
    def test_invalid_email_raises(self, bad: str) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_email(bad)
        assert exc.value.details.get("field") == "email"


class TestValidatePassword:
    def test_valid_password_returned_as_is(self) -> None:
        assert validate_password("Password1!") == "Password1!"

    @pytest.mark.parametrize(
        "bad,reason",
        [
            ("Sh0rt!", "less than 8"),
            ("alllower1!", "uppercase"),
            ("ALLUPPER1!", "lowercase"),
            ("NoDigits!", "digit"),
            ("NoSpecial1", "special"),
        ],
    )
    def test_invalid_password_raises(self, bad: str, reason: str) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_password(bad)
        assert exc.value.details.get("field") == "password"


class TestValidateFullName:
    def test_strips_and_returns(self) -> None:
        assert validate_full_name("  Alice ") == "Alice"

    @pytest.mark.parametrize("bad", ["", " ", "A"])
    def test_too_short_raises(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            validate_full_name(bad)

    def test_max_length_boundary(self) -> None:
        assert validate_full_name("a" * 255) == "a" * 255

    def test_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            validate_full_name("a" * 256)

    def test_unicode_name_passes(self) -> None:
        assert validate_full_name("नमस्ते 你好") == "नमस्ते 你好"


class TestValidatePhoneNumber:
    def test_empty_returns_none(self) -> None:
        assert validate_phone_number("") is None

    def test_whitespace_normalised(self) -> None:
        assert validate_phone_number("  +1 (415) 555-1234  ") == "+1 (415) 555-1234"

    def test_too_few_digits_raises(self) -> None:
        with pytest.raises(ValidationError):
            validate_phone_number("123")


class TestValidateFileType:
    def test_allowed_extension(self) -> None:
        assert validate_file_type("photo.JPG") == "jpg"

    def test_no_extension_raises(self) -> None:
        with pytest.raises(ValidationError):
            validate_file_type("README")

    def test_disallowed_extension_raises(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_file_type("hack.exe")
        assert "not allowed" in exc.value.message

    def test_empty_filename_raises(self) -> None:
        with pytest.raises(ValidationError):
            validate_file_type("")


class TestValidateFileSize:
    def test_under_cap_returns_value(self) -> None:
        assert validate_file_size(1024) == 1024

    def test_over_cap_raises(self) -> None:
        with pytest.raises(ValidationError):
            validate_file_size(10 ** 12)


class TestValidateMessageContent:
    def test_strips_and_returns(self) -> None:
        assert validate_message_content("  hi  ") == "hi"

    def test_empty_after_strip_raises(self) -> None:
        with pytest.raises(ValidationError):
            validate_message_content("    ")

    def test_max_length_boundary(self) -> None:
        s = "a" * 4096
        assert validate_message_content(s) == s

    def test_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            validate_message_content("a" * 4097)

    def test_unicode_payload_passes(self) -> None:
        msg = "Hello नमस्ते 你好 😀 عربي"
        assert validate_message_content(msg) == msg

    def test_custom_max_length_respected(self) -> None:
        with pytest.raises(ValidationError):
            validate_message_content("abcdef", max_length=3)


class TestValidateUrlSlug:
    @pytest.mark.parametrize("ok", ["a", "abc", "abc-def", "a1-b2-c3"])
    def test_valid(self, ok: str) -> None:
        assert validate_url_slug(ok) == ok

    @pytest.mark.parametrize("bad", ["", "Abc", "abc_def", "-leading", "trailing-", "a..b"])
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            validate_url_slug(bad)
