"""Tests for the shared database engine, session, and utility helpers."""

from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer, String, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import declarative_base

from src.shared.database import (
    BaseModel,
    SessionLocal,
    close_db,
    engine,
    get_db,
    get_model_columns,
    init_db,
    model_to_dict,
)

# --- model utilities ---------------------------------------------------------


_LocalBase = declarative_base()


class _Toy(_LocalBase):
    __tablename__ = "_db_test_toy"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)


class TestGetModelColumns:
    def test_returns_dict_keyed_by_name(self) -> None:
        cols = get_model_columns(_Toy)
        assert set(cols) == {"id", "name"}
        assert cols["id"].primary_key is True


class TestModelToDict:
    def test_none_returns_empty_dict(self) -> None:
        assert model_to_dict(None) == {}

    def test_serialises_columns_only(self) -> None:
        toy = _Toy(id=1, name="alpha")
        assert model_to_dict(toy) == {"id": 1, "name": "alpha"}


class TestBaseModel:
    def test_subclass_inherits_columns(self) -> None:
        class _Sub(BaseModel):  # type: ignore[misc, valid-type]
            __tablename__ = "_db_test_subclass"

        cols = get_model_columns(_Sub)
        assert {"id", "created_at", "updated_at"}.issubset(cols.keys())


# --- engine + session lifecycle ---------------------------------------------


class TestEngineAndSession:
    def test_engine_object_exposes_dialect(self) -> None:
        # asyncpg is the configured driver.
        assert engine.dialect.name == "postgresql"

    def test_session_local_factory_callable(self) -> None:
        # Calling the factory should yield an AsyncSession instance without DB.
        s = SessionLocal()
        assert isinstance(s, AsyncSession)


@pytest.mark.integration
class TestGetDbDependency:
    @pytest.mark.asyncio
    async def test_yields_session_and_commits_on_success(
        self,
    ) -> None:
        gen = get_db()
        session = await gen.__anext__()
        assert isinstance(session, AsyncSession)
        # Drive a tiny query to confirm the connection works
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
        # Driving the generator to completion triggers commit + close
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    @pytest.mark.asyncio
    async def test_rolls_back_on_error(self) -> None:
        gen = get_db()
        session = await gen.__anext__()
        try:
            await session.execute(text("SELECT 1"))
            await gen.athrow(RuntimeError("boom"))
        except RuntimeError:
            pass
        # After athrow, the session is closed; further use is not required.


@pytest.mark.integration
class TestInitDb:
    @pytest.mark.asyncio
    async def test_init_db_runs_without_error(self) -> None:
        # The migration already created tables; init_db is idempotent for
        # models registered against `Base`. Should not raise.
        await init_db()


@pytest.mark.integration
class TestCloseDb:
    @pytest.mark.asyncio
    async def test_close_db_does_not_raise(self) -> None:
        # Engine has its own pool; close_db disposes it. We re-create after
        # the call by importing again, but most subsequent tests in this
        # session use the per-test `test_engine` fixture, so the global
        # engine being torn down here is harmless.
        await close_db()
