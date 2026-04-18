"""Validation utilities for DuttaMessenger.

Provides reusable validators for common fields like email,
password strength, file types, etc.
"""

import re
from re import Pattern

from src.config import settings
from src.shared.exceptions import ValidationError

# Regex patterns
EMAIL_PATTERN: Pattern[str] = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
PASSWORD_MIN_LENGTH: int = 8
ALLOWED_FILE_TYPES = set(settings.ALLOWED_FILE_TYPES.split(","))


def validate_email(email: str) -> str:
    """Validate email address format.

    Args:
        email: Email address to validate.

    Returns:
        Email if valid.

    Raises:
        ValidationError: If email format is invalid.
    """
    email = email.strip().lower()
    if not email or not EMAIL_PATTERN.match(email):
        raise ValidationError("Invalid email format", field="email")
    return email


def validate_password(password: str) -> str:
    """Validate password strength.

    Requirements:
    - Minimum 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character

    Args:
        password: Password to validate.

    Returns:
        Password if valid.

    Raises:
        ValidationError: If password doesn't meet requirements.
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValidationError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters",
            field="password",
        )

    if not re.search(r"[A-Z]", password):
        raise ValidationError(
            "Password must contain at least one uppercase letter",
            field="password",
        )

    if not re.search(r"[a-z]", password):
        raise ValidationError(
            "Password must contain at least one lowercase letter",
            field="password",
        )

    if not re.search(r"\d", password):
        raise ValidationError(
            "Password must contain at least one digit",
            field="password",
        )

    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        raise ValidationError(
            "Password must contain at least one special character",
            field="password",
        )

    return password


def validate_full_name(name: str) -> str:
    """Validate full name.

    Args:
        name: Full name to validate.

    Returns:
        Name if valid.

    Raises:
        ValidationError: If name is invalid.
    """
    name = name.strip()
    if not name or len(name) < 2:
        raise ValidationError("Name must be at least 2 characters", field="full_name")
    if len(name) > 255:
        raise ValidationError("Name must be less than 255 characters", field="full_name")
    return name


def validate_phone_number(phone: str) -> str | None:
    """Validate phone number format.

    Args:
        phone: Phone number to validate.

    Returns:
        Phone number if valid, None if empty.

    Raises:
        ValidationError: If phone format is invalid.
    """
    if not phone:
        return None

    phone = phone.strip()
    # Simple validation: at least 10 digits
    digits_only = re.sub(r"\D", "", phone)
    if len(digits_only) < 10:
        raise ValidationError("Phone number must contain at least 10 digits", field="phone_number")
    return phone


def validate_file_type(file_name: str) -> str:
    """Validate file type based on extension.

    Args:
        file_name: File name to validate.

    Returns:
        File extension if valid.

    Raises:
        ValidationError: If file type not allowed.
    """
    if not file_name:
        raise ValidationError("File name is required", field="file_name")

    parts = file_name.rsplit(".", 1)
    if len(parts) != 2:
        raise ValidationError("File must have an extension", field="file_name")

    extension = parts[1].lower()
    if extension not in ALLOWED_FILE_TYPES:
        raise ValidationError(
            f"File type .{extension} not allowed. Allowed types: {', '.join(ALLOWED_FILE_TYPES)}",
            field="file_type",
        )

    return extension


def validate_file_size(file_size: int) -> int:
    """Validate file size.

    Args:
        file_size: File size in bytes.

    Returns:
        File size if valid.

    Raises:
        ValidationError: If file too large.
    """
    if file_size > settings.MAX_FILE_SIZE:
        max_mb = settings.MAX_FILE_SIZE / (1024 * 1024)
        raise ValidationError(
            f"File size exceeds maximum of {max_mb:.0f}MB",
            field="file_size",
        )
    return file_size


def validate_message_content(content: str, max_length: int = 4096) -> str:
    """Validate message content.

    Args:
        content: Message content.
        max_length: Maximum message length.

    Returns:
        Content if valid.

    Raises:
        ValidationError: If content invalid.
    """
    content = content.strip()
    if not content:
        raise ValidationError("Message cannot be empty", field="content")
    if len(content) > max_length:
        raise ValidationError(
            f"Message cannot exceed {max_length} characters",
            field="content",
        )
    return content


def validate_url_slug(slug: str) -> str:
    """Validate URL slug format (kebab-case, alphanumeric and dashes).

    Args:
        slug: URL slug to validate.

    Returns:
        Slug if valid.

    Raises:
        ValidationError: If slug format invalid.
    """
    if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", slug):
        raise ValidationError(
            "Slug must be lowercase alphanumeric with hyphens only",
            field="slug",
        )
    return slug
