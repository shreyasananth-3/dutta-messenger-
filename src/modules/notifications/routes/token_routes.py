"""HTTP routes for FCM token lifecycle."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.notifications.models.request_models import RegisterTokenRequest
from src.modules.notifications.models.response_models import (
    FcmTokenResponse,
    RegisterTokenResponse,
)
from src.modules.notifications.services.token_service import TokenService
from src.shared.database import get_db
from src.shared.middleware.auth import get_current_user
from src.shared.responses import success_response

logger = structlog.get_logger()
router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post(
    "/tokens",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def register_token(
    data: RegisterTokenRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Register (or reactivate) an FCM device token for the caller."""
    row, reused = await TokenService.register_token(
        db,
        user_id=current_user["user_id"],
        institution_id=current_user["institution_id"],
        token=data.token,
        device_name=data.device_name,
        device_type=data.device_type,
    )
    return success_response(
        RegisterTokenResponse(
            token=FcmTokenResponse.model_validate(row),
            reused=reused,
        )
    )


@router.delete(
    "/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_token(
    token_id: uuid.UUID,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-deactivate one of the caller's FCM tokens."""
    await TokenService.revoke_token(
        db,
        user_id=current_user["user_id"],
        institution_id=current_user["institution_id"],
        token_id=token_id,
    )
