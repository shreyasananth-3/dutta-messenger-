"""Cross-module WebSocket connection registry, keyed by user_id.

The chat WS route owns the per-conversation registry — appropriate for
broadcasting `message.new` and friends. But other modules sometimes need
to push a frame to *every* device a single user has open. The clearest
example is ACL: when a Super-Admin promotes user X, every active
session of X (laptop, phone, …) should re-fetch its profile within
~1 second so the new role takes effect without a sign-out (audit 4.7
cross-device revocation gap, fed by Shreyas's feedback #8).

Walking the conversation registry to find user X's connections is
correct but expensive and couples ACL to chat. A second index keyed by
user_id keeps the cost O(1) and the dependency one-way.

This is in-process by design — multi-worker / multi-host deployments
will swap the dict for a Redis pub/sub fanout. The contract this module
exposes (`register` / `unregister` / `broadcast_to_user`) is the same
in either world.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import structlog
from fastapi import WebSocket

logger = structlog.get_logger()

_user_connections: dict[str, list[WebSocket]] = defaultdict(list)


def register(user_id: str, ws: WebSocket) -> None:
    """Add `ws` to the set of live sockets owned by `user_id`."""
    _user_connections[user_id].append(ws)


def unregister(user_id: str, ws: WebSocket) -> None:
    """Remove `ws` from the user's set if present (no-op otherwise)."""
    if user_id in _user_connections:
        try:
            _user_connections[user_id].remove(ws)
        except ValueError:
            pass


async def broadcast_to_user(user_id: str, frame: dict[str, Any]) -> None:
    """Best-effort send `frame` to every active WS owned by `user_id`.

    Sends to dead/half-open sockets are caught and the socket is evicted
    from the registry so we don't accumulate zombies.
    """
    dead: list[WebSocket] = []
    for ws in list(_user_connections.get(user_id, [])):
        try:
            await ws.send_json(frame)
        except Exception as exc:  # noqa: BLE001 - best-effort fanout
            logger.debug("realtime_send_failed", user_id=user_id, error=str(exc))
            dead.append(ws)
    for ws in dead:
        unregister(user_id, ws)
