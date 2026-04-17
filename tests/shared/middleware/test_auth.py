"""Unit + integration tests for JWT middleware."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.security import HTTPAuthorizationCredentials
from httpx import ASGITransport, AsyncClient
from jose import jwt

from src.config import settings
from src.shared.exceptions import AuthenticationError
from src.shared.middleware.auth import (
    create_access_token,
    create_refresh_token,
    get_current_user,
)


class TestCreateAccessToken:
    def test_token_decodes_with_expected_claims(self) -> None:
        uid = uuid.uuid4()
        inst = uuid.uuid4()
        token = create_access_token(uid, inst)
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert payload["sub"] == str(uid)
        assert payload["inst"] == str(inst)
        assert "exp" in payload
        assert "iat" in payload

    def test_explicit_expires_delta_respected(self) -> None:
        token = create_access_token(
            uuid.uuid4(), uuid.uuid4(), expires_delta=timedelta(seconds=1)
        )
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        # exp - iat ≈ 1 second
        assert payload["exp"] - payload["iat"] == 1


class TestCreateRefreshToken:
    def test_carries_type_claim(self) -> None:
        token = create_refresh_token(uuid.uuid4(), uuid.uuid4())
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert payload["type"] == "refresh"


class TestGetCurrentUserDirect:
    @pytest.mark.asyncio
    async def test_valid_token_returns_user_dict(self) -> None:
        uid = uuid.uuid4()
        inst = uuid.uuid4()
        token = create_access_token(uid, inst)
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        result = await get_current_user(credentials=creds, db=None)  # type: ignore[arg-type]
        assert result["user_id"] == uid
        assert result["institution_id"] == inst

    @pytest.mark.asyncio
    async def test_garbage_token_raises_authentication_error(self) -> None:
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.token")
        with pytest.raises(AuthenticationError):
            await get_current_user(credentials=creds, db=None)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_token_missing_claims_raises(self) -> None:
        token = jwt.encode(
            {"foo": "bar"}, settings.SECRET_KEY, algorithm=settings.ALGORITHM
        )
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(AuthenticationError, match="missing required claims"):
            await get_current_user(credentials=creds, db=None)  # type: ignore[arg-type]


class TestGetCurrentUserOverHttp:
    @pytest.mark.asyncio
    async def test_protected_route_round_trip(self) -> None:
        app = FastAPI()

        @app.get("/me")
        async def me(user: dict = Depends(get_current_user)) -> dict:
            return {"user_id": str(user["user_id"]), "inst": str(user["institution_id"])}

        uid = uuid.uuid4()
        inst = uuid.uuid4()
        token = create_access_token(uid, inst)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            ok = await c.get("/me", headers={"Authorization": f"Bearer {token}"})
            assert ok.status_code == 200
            assert ok.json() == {"user_id": str(uid), "inst": str(inst)}

            no_auth = await c.get("/me")
            # HTTPBearer auto-error returns 401 or 403 depending on FastAPI version.
            assert no_auth.status_code in (401, 403)
