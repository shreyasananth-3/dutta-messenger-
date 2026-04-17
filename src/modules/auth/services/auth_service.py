"""Authentication service for DuttaMessenger.

Handles user registration, login, token management,
and institutional invite workflow.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.shared.exceptions import (
    ConflictError,
    AuthenticationError,
    NotFoundError,
    ValidationError,
)
from src.shared.middleware.auth import create_access_token, create_refresh_token
from src.shared.utils.datetime_utils import add_hours, get_utc_now
from src.shared.utils.validators import (
    validate_email,
    validate_password,
    validate_full_name,
    validate_phone_number,
)
from src.modules.auth.models.db_models import (
    Institution,
    User,
    UserInvitation,
    RefreshToken,
)
from src.modules.auth.models.response_models import (
    InstitutionResponse,
    UserResponse,
    InvitationResponse,
)

logger = structlog.get_logger()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    """Service for authentication and authorization operations."""

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password for storage.

        Args:
            password: Plain text password.

        Returns:
            Hashed password.
        """
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash.

        Args:
            plain_password: Plain text password to verify.
            hashed_password: Hashed password from database.

        Returns:
            True if password matches, False otherwise.
        """
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    async def create_institution(
        db: AsyncSession,
        name: str,
        description: str | None = None,
        domain: str | None = None,
        logo_url: str | None = None,
        subscription_tier: str = "free",
        max_users: int = 100,
        max_groups: int = 500,
    ) -> Institution:
        """Create a new institution.

        Args:
            db: Database session.
            name: Institution name (must be unique).
            description: Institution description.
            domain: Organization domain for auto-registration.
            logo_url: Institution logo URL.
            subscription_tier: Subscription level.
            max_users: Maximum users allowed.
            max_groups: Maximum groups allowed.

        Returns:
            Created institution.

        Raises:
            ConflictError: If institution with same name already exists.
        """
        # Check if institution already exists
        result = await db.execute(
            select(Institution).where(Institution.name == name)
        )
        if result.scalars().first():
            logger.warning("institution_creation_duplicate", name=name)
            raise ConflictError(f"Institution '{name}' already exists", "Institution")

        institution = Institution(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            domain=domain,
            logo_url=logo_url,
            subscription_tier=subscription_tier,
            max_users=max_users,
            max_groups=max_groups,
        )

        db.add(institution)
        await db.flush()

        logger.info(
            "institution_created",
            institution_id=institution.id,
            name=name,
            domain=domain,
        )

        return institution

    @staticmethod
    async def register_user(
        db: AsyncSession,
        institution_id: str,
        email: str,
        password: str,
        full_name: str,
        phone_number: str | None = None,
    ) -> User:
        """Register a new user in an institution.

        Args:
            db: Database session.
            institution_id: Target institution ID.
            email: User email (unique within institution).
            password: User password (will be hashed).
            full_name: User full name.
            phone_number: Optional phone number.

        Returns:
            Created user.

        Raises:
            NotFoundError: If institution not found.
            ConflictError: If user with email already exists in institution.
            ValidationError: If email, password, or name invalid.
        """
        # Validate inputs
        email = validate_email(email)
        password = validate_password(password)
        full_name = validate_full_name(full_name)
        if phone_number:
            phone_number = validate_phone_number(phone_number)

        # Check institution exists
        result = await db.execute(
            select(Institution).where(Institution.id == institution_id)
        )
        institution = result.scalars().first()
        if not institution:
            raise NotFoundError("Institution", institution_id)

        # Check if user already exists in institution
        result = await db.execute(
            select(User).where(
                User.institution_id == institution_id,
                User.email == email,
            )
        )
        if result.scalars().first():
            logger.warning(
                "user_registration_duplicate",
                institution_id=institution_id,
                email=email,
            )
            raise ConflictError("User with this email already exists in institution")

        # Create user
        user = User(
            id=str(uuid.uuid4()),
            institution_id=institution_id,
            email=email,
            password_hash=AuthService.hash_password(password),
            full_name=full_name,
            phone_number=phone_number,
            status="offline",
            is_active=True,
        )

        db.add(user)
        await db.flush()

        logger.info(
            "user_registered",
            user_id=user.id,
            institution_id=institution_id,
            email=email,
        )

        return user

    @staticmethod
    async def login(
        db: AsyncSession,
        email: str,
        password: str,
        institution_id: str | None = None,
    ) -> tuple[User, str, str]:
        """Authenticate user and generate tokens.

        Args:
            db: Database session.
            email: User email.
            password: User password (will be verified).
            institution_id: Optional institution ID for multi-tenant lookup.

        Returns:
            Tuple of (user, access_token, refresh_token).

        Raises:
            AuthenticationError: If email/password wrong or user inactive.
        """
        email = email.strip().lower()

        # Find user
        query = select(User).where(User.email == email)
        if institution_id:
            query = query.where(User.institution_id == institution_id)

        result = await db.execute(query)
        user = result.scalars().first()

        if not user:
            logger.warning("login_failed_user_not_found", email=email)
            raise AuthenticationError("Invalid email or password")

        # Check password
        if not AuthService.verify_password(password, user.password_hash):
            logger.warning("login_failed_invalid_password", email=email)
            raise AuthenticationError("Invalid email or password")

        # Check if user is active
        if not user.is_active:
            logger.warning("login_failed_inactive_user", user_id=user.id)
            raise AuthenticationError("User account is inactive")

        # Check if user is deleted
        if user.deleted_at:
            logger.warning("login_failed_deleted_user", user_id=user.id)
            raise AuthenticationError("User account has been deleted")

        # Generate tokens
        access_token = create_access_token(
            user_id=uuid.UUID(user.id),
            institution_id=uuid.UUID(user.institution_id),
        )
        refresh_token = create_refresh_token(
            user_id=uuid.UUID(user.id),
            institution_id=uuid.UUID(user.institution_id),
        )

        # Store refresh token hash
        refresh_token_hash = pwd_context.hash(refresh_token)
        expires_at = get_utc_now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

        db_refresh_token = RefreshToken(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=refresh_token_hash,
            expires_at=expires_at,
        )
        db.add(db_refresh_token)
        await db.flush()

        # Update last_seen_at
        user.last_seen_at = get_utc_now()
        await db.flush()

        logger.info("user_logged_in", user_id=user.id, email=email)

        return user, access_token, refresh_token

    @staticmethod
    async def refresh_access_token(
        db: AsyncSession,
        user_id: uuid.UUID,
        institution_id: uuid.UUID,
    ) -> tuple[str, str]:
        """Generate new access token using valid user session.

        Args:
            db: Database session.
            user_id: User ID.
            institution_id: Institution ID.

        Returns:
            Tuple of (new_access_token, new_refresh_token).

        Raises:
            NotFoundError: If user not found.
            AuthenticationError: If user inactive.
        """
        # Find user
        result = await db.execute(
            select(User).where(
                User.id == str(user_id),
                User.institution_id == str(institution_id),
            )
        )
        user = result.scalars().first()

        if not user:
            raise NotFoundError("User", str(user_id))

        if not user.is_active:
            raise AuthenticationError("User account is inactive")

        # Generate new tokens
        access_token = create_access_token(user_id, institution_id)
        refresh_token = create_refresh_token(user_id, institution_id)

        # Store new refresh token hash
        refresh_token_hash = pwd_context.hash(refresh_token)
        expires_at = get_utc_now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

        db_refresh_token = RefreshToken(
            id=str(uuid.uuid4()),
            user_id=str(user_id),
            token_hash=refresh_token_hash,
            expires_at=expires_at,
        )
        db.add(db_refresh_token)
        await db.flush()

        logger.info("token_refreshed", user_id=str(user_id))

        return access_token, refresh_token

    @staticmethod
    async def create_invitation(
        db: AsyncSession,
        institution_id: str,
        email: str,
        invited_by_user_id: str,
        expires_in_hours: int = 48,
    ) -> UserInvitation:
        """Create an invitation for a user to join institution.

        Args:
            db: Database session.
            institution_id: Target institution.
            email: Email to invite.
            invited_by_user_id: User creating invitation.
            expires_in_hours: Invitation expiration (default 48 hours).

        Returns:
            Created invitation.

        Raises:
            NotFoundError: If institution or inviting user not found.
            ValidationError: If email invalid.
            ConflictError: If user already in institution.
        """
        email = validate_email(email)

        # Check institution exists
        result = await db.execute(
            select(Institution).where(Institution.id == institution_id)
        )
        if not result.scalars().first():
            raise NotFoundError("Institution", institution_id)

        # Check inviting user exists and belongs to institution
        result = await db.execute(
            select(User).where(
                User.id == invited_by_user_id,
                User.institution_id == institution_id,
            )
        )
        if not result.scalars().first():
            raise NotFoundError("User", invited_by_user_id)

        # Check if user already in institution
        result = await db.execute(
            select(User).where(
                User.institution_id == institution_id,
                User.email == email,
            )
        )
        if result.scalars().first():
            raise ConflictError("User already exists in this institution")

        # Create invitation
        token = secrets.token_urlsafe(32)
        expires_at = add_hours(hours=expires_in_hours)

        invitation = UserInvitation(
            id=str(uuid.uuid4()),
            institution_id=institution_id,
            email=email,
            invited_by_user_id=invited_by_user_id,
            token=token,
            expires_at=expires_at,
        )

        db.add(invitation)
        await db.flush()

        logger.info(
            "invitation_created",
            invitation_id=invitation.id,
            institution_id=institution_id,
            email=email,
            expires_at=expires_at,
        )

        return invitation

    @staticmethod
    async def accept_invitation(
        db: AsyncSession,
        token: str,
        password: str,
        full_name: str,
    ) -> User:
        """Accept an invitation and create user account.

        Args:
            db: Database session.
            token: Invitation token.
            password: New user password.
            full_name: User full name.

        Returns:
            Created user.

        Raises:
            NotFoundError: If invitation not found or expired.
            ConflictError: If user already exists.
            ValidationError: If password or name invalid.
        """
        password = validate_password(password)
        full_name = validate_full_name(full_name)

        # Find invitation
        result = await db.execute(
            select(UserInvitation).where(UserInvitation.token == token)
        )
        invitation = result.scalars().first()

        if not invitation:
            raise NotFoundError("Invitation", token)

        # Check if expired
        if invitation.expires_at < get_utc_now():
            logger.warning("invitation_expired", invitation_id=invitation.id)
            raise ValidationError("Invitation has expired", field="token")

        # Check if already accepted
        if invitation.accepted_at:
            raise ConflictError("Invitation has already been accepted")

        # Register user
        user = await AuthService.register_user(
            db=db,
            institution_id=invitation.institution_id,
            email=invitation.email,
            password=password,
            full_name=full_name,
        )

        # Mark invitation as accepted
        invitation.accepted_at = get_utc_now()
        invitation.accepted_user_id = user.id

        logger.info(
            "invitation_accepted",
            invitation_id=invitation.id,
            user_id=user.id,
        )

        return user

    @staticmethod
    async def change_password(
        db: AsyncSession,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> User:
        """Change user password.

        Args:
            db: Database session.
            user_id: User ID.
            current_password: Current password for verification.
            new_password: New password.

        Returns:
            Updated user.

        Raises:
            NotFoundError: If user not found.
            AuthenticationError: If current password wrong.
            ValidationError: If new password invalid.
        """
        new_password = validate_password(new_password)

        # Find user
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalars().first()

        if not user:
            raise NotFoundError("User", user_id)

        # Verify current password
        if not AuthService.verify_password(current_password, user.password_hash):
            logger.warning("password_change_invalid_current", user_id=user_id)
            raise AuthenticationError("Current password is incorrect")

        # Update password
        user.password_hash = AuthService.hash_password(new_password)
        await db.flush()

        logger.info("password_changed", user_id=user_id)

        return user
