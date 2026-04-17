"""Database configuration and session management for DuttaMessenger.

Provides async SQLAlchemy engine and session factory following
async best practices with proper lifecycle management.
"""

import uuid
from typing import Any

from sqlalchemy import Column, DateTime, String, inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func

from src.config import settings

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=3600,
)

# Create session factory
SessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# Base class for all models
Base = declarative_base()


class BaseModel(Base):
    """Abstract base class for all database models.

    Provides common fields: id, created_at, updated_at.
    """

    __abstract__ = True

    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


async def get_db() -> Any:
    """Dependency to get database session in route handlers.

    Yields:
        AsyncSession: Database session for the request.

    Example:
        async def my_route(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database by creating all tables.

    Should be called once at application startup.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections.

    Should be called at application shutdown.
    """
    await engine.dispose()


def get_model_columns(model: type[Any]) -> dict[str, Any]:
    """Get all columns of a SQLAlchemy model.

    Args:
        model: SQLAlchemy model class.

    Returns:
        Dictionary mapping column names to column objects.
    """
    mapper = inspect(model)
    return {column.name: column for column in mapper.columns}


def model_to_dict(obj: Any) -> dict[str, Any]:
    """Convert SQLAlchemy model instance to dictionary.

    Args:
        obj: Model instance.

    Returns:
        Dictionary representation of the model.
    """
    if not obj:
        return {}
    return {column.name: getattr(obj, column.name) for column in inspect(obj).columns}
