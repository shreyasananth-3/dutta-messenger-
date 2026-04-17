"""Access Control List (ACL) middleware for DuttaMessenger.

Provides decorators and utilities for role-based access control
and permission checking.
"""

import uuid
from functools import wraps
from typing import Any, Callable

import structlog
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.shared.database import get_db
from src.shared.exceptions import PermissionDeniedError

logger = structlog.get_logger()


def require_permission(permission_code: str) -> Callable[..., Any]:
    """Decorator to require a specific permission for route handler.

    Args:
        permission_code: Permission code to check (e.g., 'CREATE_GROUP').

    Returns:
        Decorator function.

    Example:
        @router.post("/groups")
        @require_permission("CREATE_GROUP")
        async def create_group(data: CreateGroupRequest, ...):
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Extract user and db from kwargs
            current_user = kwargs.get("current_user")
            db = kwargs.get("db")

            if not current_user or not db:
                raise PermissionDeniedError("User context not available")

            # Check permission in database
            has_permission = await check_user_permission(
                user_id=current_user["user_id"],
                institution_id=current_user["institution_id"],
                permission_code=permission_code,
                db=db,
            )

            if not has_permission:
                logger.warning(
                    "permission_denied",
                    user_id=str(current_user["user_id"]),
                    permission=permission_code,
                )
                raise PermissionDeniedError(
                    f"You don't have permission to perform this action: {permission_code}"
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_role(role_name: str) -> Callable[..., Any]:
    """Decorator to require a specific role for route handler.

    Args:
        role_name: Role name to check (e.g., 'admin', 'moderator').

    Returns:
        Decorator function.

    Example:
        @router.delete("/users/{user_id}")
        @require_role("admin")
        async def delete_user(user_id: str, ...):
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_user = kwargs.get("current_user")
            db = kwargs.get("db")

            if not current_user or not db:
                raise PermissionDeniedError("User context not available")

            # Check role in database
            has_role = await check_user_role(
                user_id=current_user["user_id"],
                institution_id=current_user["institution_id"],
                role_name=role_name,
                db=db,
            )

            if not has_role:
                logger.warning(
                    "role_check_failed",
                    user_id=str(current_user["user_id"]),
                    required_role=role_name,
                )
                raise PermissionDeniedError(f"You must have {role_name} role")

            return await func(*args, **kwargs)

        return wrapper

    return decorator


async def check_user_permission(
    user_id: uuid.UUID,
    institution_id: uuid.UUID,
    permission_code: str,
    db: AsyncSession,
) -> bool:
    """Check if user has a specific permission.

    Args:
        user_id: User ID.
        institution_id: Institution ID.
        permission_code: Permission code to check.
        db: Database session.

    Returns:
        True if user has permission, False otherwise.
    """
    from src.modules.acl.models.db_models import UserRole, Role, RolePermission, Permission

    # Query: user -> roles -> permissions
    result = await db.execute(
        select(Permission).join(
            RolePermission,
            RolePermission.permission_id == Permission.id,
        )
        .join(
            Role,
            Role.id == RolePermission.role_id,
        )
        .join(
            UserRole,
            UserRole.role_id == Role.id,
        )
        .where(
            UserRole.user_id == str(user_id),
            Role.institution_id == str(institution_id),
            Permission.code == permission_code,
        )
    )

    return result.scalars().first() is not None


async def check_user_role(
    user_id: uuid.UUID,
    institution_id: uuid.UUID,
    role_name: str,
    db: AsyncSession,
) -> bool:
    """Check if user has a specific role.

    Args:
        user_id: User ID.
        institution_id: Institution ID.
        role_name: Role name to check.
        db: Database session.

    Returns:
        True if user has role, False otherwise.
    """
    from src.modules.acl.models.db_models import UserRole, Role

    result = await db.execute(
        select(Role).join(
            UserRole,
            UserRole.role_id == Role.id,
        )
        .where(
            UserRole.user_id == str(user_id),
            Role.institution_id == str(institution_id),
            Role.name == role_name,
        )
    )

    return result.scalars().first() is not None


async def check_group_membership(
    user_id: uuid.UUID,
    group_id: uuid.UUID,
    db: AsyncSession,
) -> bool:
    """Check if user is a member of a group.

    Args:
        user_id: User ID.
        group_id: Group ID.
        db: Database session.

    Returns:
        True if user is a member, False otherwise.
    """
    from src.modules.groups.models.db_models import GroupMember

    result = await db.execute(
        select(GroupMember).where(
            GroupMember.user_id == str(user_id),
            GroupMember.group_id == str(group_id),
        )
    )

    return result.scalars().first() is not None
