"""Production FCM client backed by firebase-admin.

Only imported when `FCM_MOCK_MODE=False` (production). Kept in a separate
module so CI/dev do not pay the import cost or require
firebase-admin's native dependencies to be present.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.modules.notifications.tasks.push_task import FcmClient, FcmResponse

logger = structlog.get_logger()


class FirebaseAdminClient(FcmClient):  # pragma: no cover - thin SDK wrapper
    """Sends multicast pushes via the firebase-admin SDK."""

    def __init__(self) -> None:
        """Lazy-initialise firebase-admin on first send."""
        import firebase_admin
        from firebase_admin import credentials

        from src.config import settings

        if not firebase_admin._apps:
            cred = credentials.Certificate(
                {
                    "type": "service_account",
                    "project_id": settings.FCM_PROJECT_ID,
                    "private_key_id": settings.FCM_PRIVATE_KEY_ID,
                    "private_key": settings.FCM_PRIVATE_KEY,
                    "client_email": settings.FCM_CLIENT_EMAIL,
                }
            )
            firebase_admin.initialize_app(cred)

    def send_multicast(
        self,
        *,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, Any] | None,
    ) -> FcmResponse:
        from firebase_admin import messaging

        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
        )
        try:
            resp = messaging.send_multicast(message)
        except Exception as exc:
            logger.error("fcm_transport_error", error=str(exc))
            return FcmResponse(
                success_count=0,
                failure_count=len(tokens),
                unregistered_tokens=[],
                error=str(exc),
            )

        unregistered: list[str] = []
        for idx, single in enumerate(resp.responses):
            if single.exception is None:
                continue
            code = getattr(single.exception, "code", "")
            if "UNREGISTERED" in str(code):
                unregistered.append(tokens[idx])

        return FcmResponse(
            success_count=resp.success_count,
            failure_count=resp.failure_count,
            unregistered_tokens=unregistered,
        )
