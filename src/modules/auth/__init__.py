"""Auth module for DuttaMessenger.

Handles authentication, user registration, JWT management,
and institutional invite workflow.
"""

from src.modules.auth.router import router

__all__ = ["router"]
