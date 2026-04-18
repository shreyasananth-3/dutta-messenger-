"""Auth route handlers for DuttaMessenger."""

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.modules.auth.models.request_models import (
    ChangePasswordRequest,
    CreateInstitutionRequest,
    InviteUserRequest,
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
)
from src.modules.auth.models.response_models import (
    InstitutionResponse,
    InvitationResponse,
    InviteUserResponse,
    LoginResponse,
    RefreshTokenResponse,
    RegistrationResponse,
    UserResponse,
)
from src.modules.auth.services.auth_service import AuthService
from src.shared.database import get_db
from src.shared.exceptions import AppException, InternalServerError
from src.shared.middleware.auth import get_current_user
from src.shared.responses import success_response

logger = structlog.get_logger()
router = APIRouter(tags=["auth"])


@router.post(
    "/institutions",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
)
async def create_institution(
    data: CreateInstitutionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new institution.

    Args:
        data: Institution creation request.
        db: Database session.

    Returns:
        Created institution details.
    """
    try:
        institution = await AuthService.create_institution(
            db=db,
            name=data.name,
            description=data.description,
            domain=data.domain,
            logo_url=data.logo_url,
            subscription_tier=data.subscription_tier,
            max_users=data.max_users,
            max_groups=data.max_groups,
        )
        await db.commit()

        return success_response(InstitutionResponse.from_orm(institution))
    except AppException as e:
        await db.rollback()
        raise e.to_http_exception() from e
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error("create_institution_error", error=str(e))
        raise InternalServerError(message="Failed to create institution") from e


@router.post(
    "/auth/register",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
)
async def register(
    data: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Register a new user.

    Can be used for direct registration or via invitation link.

    Args:
        data: Registration request.
        db: Database session.

    Returns:
        User and authentication tokens.
    """
    try:
        # If invitation token provided, accept it
        if data.invitation_token:
            user = await AuthService.accept_invitation(
                db=db,
                token=data.invitation_token,
                password=data.password,
                full_name=data.full_name,
            )
            await db.commit()
            return success_response(
                RegistrationResponse(
                    user=UserResponse.from_orm(user),
                    message="Account created successfully from invitation",
                )
            )

        # Otherwise, direct registration (if institution allows)
        raise AppException(
            error_code="AUTH_DIRECT_REGISTRATION_DISABLED",
            message="Direct registration not allowed. Please use invitation link.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except AppException as e:
        await db.rollback()
        raise e.to_http_exception() from e
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error("register_error", error=str(e))
        raise InternalServerError(message="Registration failed") from e


@router.post(
    "/auth/login",
    response_model=dict[str, Any],
)
async def login(
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Authenticate user and generate tokens.

    Args:
        data: Login request with email and password.
        db: Database session.

    Returns:
        User info and JWT tokens.
    """
    try:
        user, access_token, refresh_token = await AuthService.login(
            db=db,
            email=data.email,
            password=data.password,
            institution_id=data.institution_id,
        )
        await db.commit()

        return success_response(
            LoginResponse(
                user=UserResponse.from_orm(user),
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            )
        )
    except AppException as e:
        await db.rollback()
        raise e.to_http_exception() from e
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error("login_error", error=str(e))
        raise InternalServerError(message="Login failed") from e


@router.post(
    "/auth/refresh",
    response_model=dict[str, Any],
)
async def refresh_token(
    data: RefreshTokenRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Refresh access token using valid user session.

    Args:
        data: Refresh token request.
        current_user: Current authenticated user.
        db: Database session.

    Returns:
        New access and refresh tokens.
    """
    try:
        access_token, refresh_token = await AuthService.refresh_access_token(
            db=db,
            user_id=current_user["user_id"],
            institution_id=current_user["institution_id"],
        )
        await db.commit()

        return success_response(
            RefreshTokenResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            )
        )
    except AppException as e:
        await db.rollback()
        raise e.to_http_exception() from e
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error("refresh_token_error", error=str(e))
        raise InternalServerError(message="Token refresh failed") from e


@router.post(
    "/auth/invite",
    response_model=dict[str, Any],
    status_code=status.HTTP_201_CREATED,
)
async def invite_user(
    data: InviteUserRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Invite a user to join the institution.

    Args:
        data: Invite request with email.
        current_user: Current authenticated user.
        db: Database session.

    Returns:
        Created invitation details.
    """
    try:
        invitation = await AuthService.create_invitation(
            db=db,
            institution_id=str(current_user["institution_id"]),
            email=data.email,
            invited_by_user_id=str(current_user["user_id"]),
        )
        await db.commit()

        return success_response(
            InviteUserResponse(
                invitation=InvitationResponse.from_orm(invitation),
                message=f"Invitation sent to {data.email}",
            )
        )
    except AppException as e:
        await db.rollback()
        raise e.to_http_exception() from e
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error("invite_user_error", error=str(e))
        raise InternalServerError(message="Failed to send invitation") from e


@router.post(
    "/auth/change-password",
    response_model=dict[str, Any],
)
async def change_password(
    data: ChangePasswordRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Change password for authenticated user.

    Args:
        data: Change password request.
        current_user: Current authenticated user.
        db: Database session.

    Returns:
        Updated user info.
    """
    try:
        user = await AuthService.change_password(
            db=db,
            user_id=str(current_user["user_id"]),
            current_password=data.current_password,
            new_password=data.new_password,
        )
        await db.commit()

        return success_response(UserResponse.from_orm(user))
    except AppException as e:
        await db.rollback()
        raise e.to_http_exception() from e
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error("change_password_error", error=str(e))
        raise InternalServerError(message="Failed to change password") from e
