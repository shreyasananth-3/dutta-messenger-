"""Notifications module for DuttaMessenger.

Registers FCM device tokens, fans out push notifications for new messages
via Celery, and exposes the in-app notification feed. See
`src/modules/notifications/docs/MODULE.md` for the full contract.
"""

from src.modules.notifications.router import router

__all__ = ["router"]
