"""JWT authentication middleware for DuttaMessenger.

Provides JWT token validation and current user extraction
for use in protected route handlers.
"""

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthCredentials
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.shared.database import get_db
from src.shared.exceptions import AuthenticationError

logger = structlog.get_logger()

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Extract and validate JWT token, return current user.

    Args:
        credentials: HTTP Bearer token from request.
        db: Database session.

    Returns:
        Dictionary containing user_id and institution_id.

    Raises:
        HTTPException(401): If token is invalid or expired.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        user_id: str = payload.get("sub")
        institution_id: str = payload.get("inst")

        if not user_id or not institution_id:
            logger.warning("token_missing_claims", token_preview=token[:20])
            raise AuthenticationError("Token missing required claims")

        return {
            "user_id": uuid.UUID(user_id),
            "institution_id": uuid.UUID(institution_id),
        }
    except JWTError as e:
        logger.warning("token_decode_error", error=str(e))
        raise AuthenticationError("Invalid or expired token") from e


def create_access_token(
    user_id: uuid.UUID,
    institution_id: uuid.UUID,
    expires_delta: timedelta | None = None,
) -> str:
    """Create JWT access token.

    Args:
        user_id: User ID to encode.
        institution_id: Institution ID to encode.
        expires_delta: Token expiration time (default from settings).

    Returns:
        Encoded JWT token string.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {
        "sub": str(user_id),
        "inst": str(institution_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    logger.info(
        "access_token_created",
        user_id=str(user_id),
        expires_in_minutes=expires_delta.total_seconds() / 60,
    )
    return encoded_jwt


def create_refresh_token(
    user_id: uuid.UUID,
    institution_id: uuid.UUID,
) -> str:
    """Create JWT refresh token.

    Args:
        user_id: User ID to encode.
        institution_id: Institution ID to encode.

    Returns:
        Encoded JWT token string.
    """
    expires_delta = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {
        "sub": str(user_id),
        "inst": str(institution_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
    }

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    logger.info(
        "refresh_token_created",
        user_id=str(user_id),
        expires_in_days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
    )
    return encoded_jwt
