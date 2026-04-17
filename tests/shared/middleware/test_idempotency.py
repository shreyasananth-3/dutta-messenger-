"""Unit tests for `src/shared/middleware/idempotency.py`.

Redis is mocked — at this layer we only care that the middleware matches
the `docs/design/idempotency.md` contract:

- Keys have the exact `idempotency:{inst}:{user}:{fingerprint}:{client}` shape
- Missing header → 400 IDEMPOTENCY_KEY_REQUIRED when required=True
- Non-UUID4 header → 400 IDEMPOTENCY_KEY_INVALID
- Same key + same payload → HIT, stored entry returned
- Same key + different payload → COLLISION → 409 IDEMPOTENCY_COLLISION
- New key → MISS, handler proceeds, store() persists bytes
- Redis down → IdempotencyResult(outcome="redis_down"), handler proceeds (fail-open)

Live Redis is exercised in integration tests for chat/media (Stages 4d/e).
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.shared.middleware import idempotency as idem_mod
from src.shared.middleware.idempotency import (
    DEFAULT_TTL_SECONDS,
    HEADER_NAME,
    IdempotencyCheck,
    IdempotencyResult,
    StoredIdempotencyEntry,
    build_key,
    check_idempotency,
    payload_hash,
    store_idempotency,
)


# ---------------------------------------------------------------------------
# Key + hash helpers
# ---------------------------------------------------------------------------


class TestBuildKey:
    def test_exact_shape(self) -> None:
        inst = uuid.UUID("11111111-1111-4111-8111-111111111111")
        user = uuid.UUID("22222222-2222-4222-8222-222222222222")
        client = "33333333-3333-4333-8333-333333333333"
        assert build_key(
            institution_id=inst,
            user_id=user,
            endpoint_fingerprint="chat.messages",
            client_key=client,
        ) == (
            "idempotency:11111111-1111-4111-8111-111111111111:"
            "22222222-2222-4222-8222-222222222222:chat.messages:"
            "33333333-3333-4333-8333-333333333333"
        )


class TestPayloadHash:
    def test_stable_for_identical_bytes(self) -> None:
        assert payload_hash(b'{"a":1}') == payload_hash(b'{"a":1}')

    def test_differs_for_different_bytes(self) -> None:
        assert payload_hash(b'{"a":1}') != payload_hash(b'{"a":2}')


# ---------------------------------------------------------------------------
# check_idempotency
# ---------------------------------------------------------------------------


def _fake_redis(get_return: Any = None, set_return: Any = True) -> MagicMock:
    r = MagicMock()
    r.get = AsyncMock(return_value=get_return)
    r.set = AsyncMock(return_value=set_return)
    return r


class TestCheckIdempotency:
    @pytest.mark.asyncio
    async def test_miss_when_key_absent(self) -> None:
        redis = _fake_redis(get_return=None)
        result = await check_idempotency(redis, "k", "h")
        assert result.outcome == "miss"
        assert result.stored is None

    @pytest.mark.asyncio
    async def test_hit_when_payload_hash_matches(self) -> None:
        stored_payload = {
            "status": 201,
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(b'{"id":"abc"}').decode("ascii"),
            "payload_hash": "samehash",
            "created_at": "2026-04-18T00:00:00+00:00",
        }
        redis = _fake_redis(get_return=json.dumps(stored_payload))
        result = await check_idempotency(redis, "k", "samehash")
        assert result.outcome == "hit"
        assert result.stored is not None
        assert result.stored.status == 201
        assert result.stored.body == b'{"id":"abc"}'

    @pytest.mark.asyncio
    async def test_collision_when_payload_hash_differs(self) -> None:
        stored_payload = {
            "status": 201,
            "headers": {},
            "body": base64.b64encode(b"x").decode("ascii"),
            "payload_hash": "oldhash",
            "created_at": "2026-04-18T00:00:00+00:00",
        }
        redis = _fake_redis(get_return=json.dumps(stored_payload))
        result = await check_idempotency(redis, "k", "newhash")
        assert result.outcome == "collision"

    @pytest.mark.asyncio
    async def test_redis_down_is_fail_open(self) -> None:
        redis = MagicMock()
        redis.get = AsyncMock(side_effect=ConnectionError("redis gone"))
        result = await check_idempotency(redis, "k", "h")
        assert result.outcome == "redis_down"

    @pytest.mark.asyncio
    async def test_corrupt_entry_treated_as_miss(self) -> None:
        redis = _fake_redis(get_return='{"not":"valid"}')  # missing keys
        result = await check_idempotency(redis, "k", "h")
        assert result.outcome == "miss"


# ---------------------------------------------------------------------------
# store_idempotency
# ---------------------------------------------------------------------------


class TestStoreIdempotency:
    @pytest.mark.asyncio
    async def test_writes_base64_body_with_default_ttl(self) -> None:
        redis = _fake_redis()
        await store_idempotency(
            redis,
            "k",
            "hash123",
            status=201,
            response_body=b'{"ok":true}',
        )
        redis.set.assert_awaited_once()
        args, kwargs = redis.set.call_args
        key, raw = args
        assert key == "k"
        assert kwargs["ex"] == DEFAULT_TTL_SECONDS
        data = json.loads(raw)
        assert data["status"] == 201
        assert base64.b64decode(data["body"]) == b'{"ok":true}'
        assert data["payload_hash"] == "hash123"

    @pytest.mark.asyncio
    async def test_custom_ttl(self) -> None:
        redis = _fake_redis()
        await store_idempotency(
            redis, "k", "h", status=200, response_body=b"x", ttl_seconds=60
        )
        assert redis.set.call_args.kwargs["ex"] == 60

    @pytest.mark.asyncio
    async def test_fail_silent_on_redis_error(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock(side_effect=ConnectionError("redis gone"))
        # Must NOT raise — the user's mutation already succeeded.
        await store_idempotency(
            redis, "k", "h", status=200, response_body=b"x"
        )


# ---------------------------------------------------------------------------
# IdempotencyCheck (the per-request handle)
# ---------------------------------------------------------------------------


class TestIdempotencyCheck:
    def test_is_hit_true_when_outcome_hit(self) -> None:
        stored = StoredIdempotencyEntry(
            status=201, headers={}, body=b"x", payload_hash="h", created_at="t"
        )
        check = IdempotencyCheck(
            outcome="hit",
            redis=MagicMock(),
            key="k",
            payload_digest="h",
            stored=stored,
        )
        assert check.is_hit is True
        assert check.is_collision is False

    def test_replay_returns_response_with_stored_bytes(self) -> None:
        stored = StoredIdempotencyEntry(
            status=201,
            headers={"Content-Type": "application/json"},
            body=b'{"id":"abc"}',
            payload_hash="h",
            created_at="t",
        )
        check = IdempotencyCheck(
            outcome="hit",
            redis=MagicMock(),
            key="k",
            payload_digest="h",
            stored=stored,
        )
        resp = check.replay()
        assert resp.status_code == 201
        assert resp.body == b'{"id":"abc"}'

    def test_replay_raises_when_no_stored(self) -> None:
        check = IdempotencyCheck(
            outcome="miss",
            redis=MagicMock(),
            key="k",
            payload_digest="h",
        )
        with pytest.raises(RuntimeError):
            check.replay()

    @pytest.mark.asyncio
    async def test_store_serialises_pydantic_model(self) -> None:
        from pydantic import BaseModel

        class _R(BaseModel):
            id: str

        redis = _fake_redis()
        check = IdempotencyCheck(
            outcome="miss",
            redis=redis,
            key="k",
            payload_digest="h",
        )
        await check.store(_R(id="abc"), status=201)
        raw = redis.set.call_args[0][1]
        stored = json.loads(raw)
        assert base64.b64decode(stored["body"]) == b'{"id":"abc"}'

    @pytest.mark.asyncio
    async def test_store_serialises_dict(self) -> None:
        redis = _fake_redis()
        check = IdempotencyCheck(
            outcome="miss",
            redis=redis,
            key="k",
            payload_digest="h",
        )
        await check.store({"id": "abc"}, status=201)
        raw = redis.set.call_args[0][1]
        stored = json.loads(raw)
        assert base64.b64decode(stored["body"]) == b'{"id": "abc"}'

    @pytest.mark.asyncio
    async def test_store_is_idempotent_within_request(self) -> None:
        redis = _fake_redis()
        check = IdempotencyCheck(
            outcome="miss",
            redis=redis,
            key="k",
            payload_digest="h",
        )
        await check.store({"a": 1})
        await check.store({"a": 2})
        # Only the first call writes; second is a no-op.
        assert redis.set.await_count == 1

    @pytest.mark.asyncio
    async def test_store_noop_when_redis_was_down(self) -> None:
        redis = _fake_redis()
        check = IdempotencyCheck(
            outcome="redis_down",
            redis=redis,
            key="k",
            payload_digest="h",
        )
        await check.store({"a": 1})
        assert redis.set.await_count == 0


# ---------------------------------------------------------------------------
# require_idempotency dependency — exception classes
# ---------------------------------------------------------------------------


class TestExceptionClasses:
    def test_missing_exception_shape(self) -> None:
        from src.shared.middleware.idempotency import IdempotencyKeyMissing

        exc = IdempotencyKeyMissing()
        assert exc.status_code == 400
        assert exc.error_code == "IDEMPOTENCY_KEY_REQUIRED"
        assert HEADER_NAME in exc.message

    def test_invalid_exception_shape(self) -> None:
        from src.shared.middleware.idempotency import IdempotencyKeyInvalid

        exc = IdempotencyKeyInvalid("garbage")
        assert exc.status_code == 400
        assert exc.error_code == "IDEMPOTENCY_KEY_INVALID"

    def test_collision_exception_uses_canonical_code(self) -> None:
        from src.shared.middleware.idempotency import IdempotencyCollision

        exc = IdempotencyCollision()
        assert exc.status_code == 409
        assert exc.error_code == "IDEMPOTENCY_COLLISION"


# ---------------------------------------------------------------------------
# require_idempotency — end-to-end via a mock Request + user
# ---------------------------------------------------------------------------


class TestRequireIdempotencyDependency:
    @pytest.mark.asyncio
    async def test_missing_header_required_raises_400(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.shared.middleware.idempotency import (
            IdempotencyKeyMissing,
            require_idempotency,
        )

        dep = require_idempotency("chat.messages")
        request = MagicMock()
        request.body = AsyncMock(return_value=b"{}")
        user = {"user_id": uuid.uuid4(), "institution_id": uuid.uuid4()}
        with pytest.raises(IdempotencyKeyMissing):
            await dep(request=request, current_user=user, idempotency_key=None)

    @pytest.mark.asyncio
    async def test_missing_header_optional_returns_noop(self) -> None:
        from src.shared.middleware.idempotency import require_idempotency

        dep = require_idempotency("chat.messages", required=False)
        request = MagicMock()
        request.body = AsyncMock(return_value=b"{}")
        user = {"user_id": uuid.uuid4(), "institution_id": uuid.uuid4()}
        check = await dep(
            request=request, current_user=user, idempotency_key=None
        )
        assert check.is_hit is False
        # store() is a no-op — should not touch anything
        await check.store({"a": 1})

    @pytest.mark.asyncio
    async def test_bad_uuid_raises_400(self) -> None:
        from src.shared.middleware.idempotency import (
            IdempotencyKeyInvalid,
            require_idempotency,
        )

        dep = require_idempotency("chat.messages")
        request = MagicMock()
        request.body = AsyncMock(return_value=b"{}")
        user = {"user_id": uuid.uuid4(), "institution_id": uuid.uuid4()}
        with pytest.raises(IdempotencyKeyInvalid):
            await dep(
                request=request, current_user=user, idempotency_key="not-a-uuid"
            )

    @pytest.mark.asyncio
    async def test_non_uuid4_shape_raises_400(self) -> None:
        """UUIDv1-shaped values (first byte of version != 4) must be rejected."""
        from src.shared.middleware.idempotency import (
            IdempotencyKeyInvalid,
            require_idempotency,
        )

        dep = require_idempotency("chat.messages")
        request = MagicMock()
        request.body = AsyncMock(return_value=b"{}")
        user = {"user_id": uuid.uuid4(), "institution_id": uuid.uuid4()}
        # UUIDv1 has "1" in the version position, not "4"
        with pytest.raises(IdempotencyKeyInvalid):
            await dep(
                request=request,
                current_user=user,
                idempotency_key="6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            )

    @pytest.mark.asyncio
    async def test_collision_raises_409(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.shared.middleware.idempotency import (
            IdempotencyCollision,
            require_idempotency,
        )

        dep = require_idempotency("chat.messages")
        client_key = str(uuid.uuid4())
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"a":"NEW"}')
        user = {"user_id": uuid.uuid4(), "institution_id": uuid.uuid4()}

        stored = {
            "status": 201,
            "headers": {},
            "body": base64.b64encode(b"x").decode("ascii"),
            "payload_hash": "OLD_hash",
            "created_at": "2026-04-18T00:00:00+00:00",
        }
        redis = _fake_redis(get_return=json.dumps(stored))

        async def _get_redis() -> MagicMock:
            return redis

        monkeypatch.setattr(idem_mod, "get_redis", _get_redis)

        with pytest.raises(IdempotencyCollision):
            await dep(
                request=request,
                current_user=user,
                idempotency_key=client_key,
            )

    @pytest.mark.asyncio
    async def test_miss_returns_live_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.shared.middleware.idempotency import require_idempotency

        dep = require_idempotency("chat.messages")
        client_key = str(uuid.uuid4())
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"a":1}')
        user = {"user_id": uuid.uuid4(), "institution_id": uuid.uuid4()}

        redis = _fake_redis(get_return=None)

        async def _get_redis() -> MagicMock:
            return redis

        monkeypatch.setattr(idem_mod, "get_redis", _get_redis)

        check = await dep(
            request=request, current_user=user, idempotency_key=client_key
        )
        assert check.is_hit is False
        assert check.outcome == "miss"
