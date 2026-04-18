"""Redis-backed Idempotency-Key helper.

Implements `docs/design/idempotency.md`:

- Key scope: `idempotency:{institution_id}:{user_id}:{endpoint_fingerprint}:{client_key}`
- TTL: 24 hours (configurable)
- Replay: byte-for-byte
- Collision: same key + different payload → 409 IDEMPOTENCY_COLLISION
- Fail-open: if Redis is down, log + metric + proceed as MISS

Public API (matches the RFC word-for-word):

    IdempotencyResult           - dataclass, outcome + stored entry
    StoredIdempotencyEntry      - dataclass, the cached response
    check_idempotency(...)      - low-level check
    store_idempotency(...)      - low-level store
    require_idempotency(...)    - FastAPI dependency factory (route-level)
    IdempotencyCheck            - per-request object: .is_hit / .replay() / .store(body, status)

Route handlers use the high-level dependency:

    @router.post("/something", status_code=201)
    async def endpoint(
        body: MyRequest,
        idem: IdempotencyCheck = Depends(require_idempotency("my.endpoint")),
        current_user=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        if idem.is_hit:
            return idem.replay()
        result = await service.do(db, current_user, body)
        await idem.store(result, status=201)
        return result

Audit semantics: on HIT, the service is never called, so `write_audit()` is
never reached → audit rows are written exactly once per real mutation.
This is the structural fix for Gap A (see `docs/MANUAL_SMOKE.md` §Gap A).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import Depends, Header, Request
from redis.asyncio import Redis

from src.shared.exceptions import AppException, ConflictError
from src.shared.middleware.auth import get_current_user
from src.shared.redis import get_redis

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TTL_SECONDS = 86_400  # 24 hours — see RFC §TTL
HEADER_NAME = "Idempotency-Key"
KEY_PREFIX = "idempotency"

# UUID4 regex — same shape the JWT and DB use for IDs. Client keys MUST be
# UUID4 per the RFC; any other shape is 400 at the dependency layer.
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

Outcome = Literal["hit", "miss", "collision", "redis_down"]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredIdempotencyEntry:
    """A previously-completed response, cached for replay."""

    status: int
    headers: dict[str, str]
    body: bytes
    payload_hash: str
    created_at: str


@dataclass
class IdempotencyResult:
    """Outcome of a `check_idempotency()` call."""

    outcome: Outcome
    stored: StoredIdempotencyEntry | None = None


# ---------------------------------------------------------------------------
# Key + payload helpers
# ---------------------------------------------------------------------------


def build_key(
    *,
    institution_id: uuid.UUID | str,
    user_id: uuid.UUID | str,
    endpoint_fingerprint: str,
    client_key: str,
) -> str:
    """Return the full Redis key. See RFC §Key layout."""
    return f"{KEY_PREFIX}:{institution_id}:{user_id}:{endpoint_fingerprint}:{client_key}"


def payload_hash(body: bytes) -> str:
    """SHA-256 hex digest of the request body. Used for collision detection."""
    return hashlib.sha256(body).hexdigest()


# ---------------------------------------------------------------------------
# Low-level check / store
# ---------------------------------------------------------------------------


async def check_idempotency(
    redis: Redis,
    key: str,
    payload_digest: str,
) -> IdempotencyResult:
    """Look up `key` and classify the outcome.

    Returns:
        HIT — stored entry found, payload hash matches. Caller should replay.
        MISS — no entry; caller should proceed.
        COLLISION — entry found but payload hash differs; caller should 409.
        REDIS_DOWN — Redis is unavailable; caller should proceed (fail-open).
    """
    try:
        raw = await redis.get(key)
    except Exception as exc:
        logger.error("idempotency_redis_unavailable", error=str(exc), key=key)
        from src.shared.observability.metrics import IDEMPOTENCY_REDIS_DOWN

        IDEMPOTENCY_REDIS_DOWN.inc()
        return IdempotencyResult(outcome="redis_down")

    if raw is None:
        return IdempotencyResult(outcome="miss")

    try:
        data = json.loads(raw)
        stored = StoredIdempotencyEntry(
            status=int(data["status"]),
            headers=dict(data.get("headers") or {}),
            body=base64.b64decode(data["body"]),
            payload_hash=str(data["payload_hash"]),
            created_at=str(data["created_at"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        # A corrupt entry is treated as MISS — next write overwrites it.
        # This is defensive; under normal operation the stored JSON is
        # always well-formed.
        logger.warning("idempotency_corrupt_entry", error=str(exc), key=key)
        return IdempotencyResult(outcome="miss")

    if stored.payload_hash != payload_digest:
        return IdempotencyResult(outcome="collision", stored=stored)

    return IdempotencyResult(outcome="hit", stored=stored)


async def store_idempotency(
    redis: Redis,
    key: str,
    payload_digest: str,
    status: int,
    response_body: bytes,
    *,
    headers: dict[str, str] | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    """Persist a completed response for future replay.

    Silently no-ops if Redis is unavailable (same fail-open stance as
    `check_idempotency`). Metric `dutta_idempotency_store_failed_total`
    fires so operators can alert on persistent outages.
    """
    entry = {
        "status": status,
        "headers": headers or {"Content-Type": "application/json"},
        "body": base64.b64encode(response_body).decode("ascii"),
        "payload_hash": payload_digest,
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        await redis.set(key, json.dumps(entry), ex=ttl_seconds)
    except Exception as exc:
        logger.error("idempotency_store_failed", error=str(exc), key=key)
        from src.shared.observability.metrics import IDEMPOTENCY_STORE_FAILED

        IDEMPOTENCY_STORE_FAILED.inc()


# ---------------------------------------------------------------------------
# Per-request dependency — what routes use
# ---------------------------------------------------------------------------


class IdempotencyCheck:
    """Per-request handle returned by `require_idempotency`.

    Routes call `.is_hit` to branch, `.replay()` to return the cached
    response, and `.store(body, status)` after the real handler succeeds.
    """

    def __init__(
        self,
        *,
        outcome: Outcome,
        redis: Redis,
        key: str,
        payload_digest: str,
        stored: StoredIdempotencyEntry | None = None,
    ) -> None:
        self._outcome = outcome
        self._redis = redis
        self._key = key
        self._payload_digest = payload_digest
        self._stored = stored
        self._stored_this_request = False

    # -- Branch helpers --------------------------------------------------

    @property
    def outcome(self) -> Outcome:
        return self._outcome

    @property
    def is_hit(self) -> bool:
        return self._outcome == "hit"

    @property
    def is_collision(self) -> bool:
        return self._outcome == "collision"

    @property
    def redis_is_down(self) -> bool:
        return self._outcome == "redis_down"

    # -- Replay / store --------------------------------------------------

    def replay(self) -> Any:
        """Return a FastAPI response built from the cached bytes.

        Callers typically just `return idem.replay()` — FastAPI happily
        returns a Response object directly.
        """
        if self._stored is None:
            raise RuntimeError("replay() called when outcome is not HIT")
        from fastapi import Response

        return Response(
            content=self._stored.body,
            status_code=self._stored.status,
            headers=self._stored.headers,
        )

    async def store(self, response: Any, *, status: int = 200) -> None:
        """Serialise `response` and persist it.

        `response` can be:
          - A Pydantic BaseModel → JSON-serialised via `.model_dump_json()`
          - A dict or list → `json.dumps`
          - Raw `bytes` → stored as-is
          - A `str` → UTF-8 encoded
          - Anything else → `str(...)` then UTF-8 encoded
        """
        if self._outcome == "redis_down" or self._stored_this_request:
            return

        body = _serialise_response(response)
        await store_idempotency(
            self._redis,
            self._key,
            self._payload_digest,
            status=status,
            response_body=body,
        )
        self._stored_this_request = True


def _serialise_response(response: Any) -> bytes:
    """Normalise whatever the handler returned to raw JSON bytes."""
    # Pydantic v2 BaseModel
    if hasattr(response, "model_dump_json"):
        serialised: str = response.model_dump_json()
        return serialised.encode("utf-8")
    if isinstance(response, bytes):
        return response
    if isinstance(response, str):
        return response.encode("utf-8")
    if isinstance(response, (dict, list)):
        return json.dumps(response, default=str).encode("utf-8")
    return str(response).encode("utf-8")


# ---------------------------------------------------------------------------
# FastAPI dependency factory
# ---------------------------------------------------------------------------


class IdempotencyKeyMissing(AppException):
    """Raised when a mandatory `Idempotency-Key` header is absent."""

    def __init__(self) -> None:
        super().__init__(
            error_code="IDEMPOTENCY_KEY_REQUIRED",
            message=f"Missing required header: {HEADER_NAME}",
            status_code=400,
            details={"header": HEADER_NAME},
        )


class IdempotencyKeyInvalid(AppException):
    """Raised when the `Idempotency-Key` header value is not a UUID4."""

    def __init__(self, value: str) -> None:
        super().__init__(
            error_code="IDEMPOTENCY_KEY_INVALID",
            message=f"{HEADER_NAME} must be a UUID4",
            status_code=400,
            details={"header": HEADER_NAME, "value": value[:64]},
        )


class IdempotencyCollision(ConflictError):
    """Raised when the same `Idempotency-Key` is reused with a different body."""

    def __init__(self) -> None:
        super().__init__(
            message=(
                f"{HEADER_NAME} was reused with a different payload. Generate "
                "a new key for the new request."
            ),
            resource_type="idempotency_key",
        )
        self.error_code = "IDEMPOTENCY_COLLISION"
        self.status_code = 409


def require_idempotency(
    endpoint_fingerprint: str,
    *,
    required: bool = True,
) -> Any:
    """Dependency factory — returns a FastAPI `Depends()`-compatible callable.

    Args:
        endpoint_fingerprint: Short stable string e.g. `"chat.messages"`.
            Prevents a key replay across different endpoints.
        required: If False, a missing header is allowed; the returned
            `IdempotencyCheck` is a no-op stub (`is_hit = False`,
            `store()` is a no-op). Use this for endpoints where
            idempotency is optional (e.g. profile updates).
    """

    async def _dep(
        request: Request,
        current_user: Annotated[dict[str, Any], Depends(get_current_user)],
        idempotency_key: Annotated[str | None, Header(alias=HEADER_NAME)] = None,
    ) -> IdempotencyCheck:
        if idempotency_key is None:
            if required:
                raise IdempotencyKeyMissing()
            return _NoopIdempotencyCheck()

        if not _UUID4_RE.match(idempotency_key):
            raise IdempotencyKeyInvalid(idempotency_key)

        body = await request.body()
        digest = payload_hash(body)
        key = build_key(
            institution_id=current_user["institution_id"],
            user_id=current_user["user_id"],
            endpoint_fingerprint=endpoint_fingerprint,
            client_key=idempotency_key,
        )

        redis = await get_redis()
        result = await check_idempotency(redis, key, digest)

        if result.outcome == "collision":
            raise IdempotencyCollision()

        return IdempotencyCheck(
            outcome=result.outcome,
            redis=redis,
            key=key,
            payload_digest=digest,
            stored=result.stored,
        )

    return _dep


class _NoopIdempotencyCheck(IdempotencyCheck):
    """Stub returned when idempotency is optional and no header was sent."""

    def __init__(self) -> None:
        # Bypass parent __init__ since we have no Redis / key.
        self._outcome = "miss"
        self._stored = None
        self._stored_this_request = False

    async def store(self, response: Any, *, status: int = 200) -> None:
        return  # no-op


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "HEADER_NAME",
    "IdempotencyCheck",
    "IdempotencyCollision",
    "IdempotencyKeyInvalid",
    "IdempotencyKeyMissing",
    "IdempotencyResult",
    "Outcome",
    "StoredIdempotencyEntry",
    "build_key",
    "check_idempotency",
    "payload_hash",
    "require_idempotency",
    "store_idempotency",
]
