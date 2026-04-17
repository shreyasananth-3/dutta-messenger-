"""Users module — profiles, search, online status, settings.

See `reference-docs/modules/users/MODULE.md` for the full contract. The
module is gated by `settings.ENABLE_USERS` in `src/main.py`.
"""

from src.modules.users.router import router

__all__ = ["router"]
