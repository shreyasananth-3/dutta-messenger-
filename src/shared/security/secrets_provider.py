"""Secrets provider abstraction.

Backend is chosen by `SECRETS_BACKEND`:
  - `env` (default): read from environment variables / `.env`.
  - `aws`:  read from AWS Secrets Manager (requires boto3 + IAM).
  - `gcp`:  read from Google Secret Manager (requires google-cloud-secret-manager).
  - `vault`: read from HashiCorp Vault KV v2 (requires hvac).

Only `env` is wired today — it's all we need for a single-school deployment.
The other branches are stubs that raise a clear error, keeping the abstraction
honest: when you need them, you add the implementation in ONE place without
hunting through business code.
"""

from __future__ import annotations

import os

_BACKEND = os.environ.get("SECRETS_BACKEND", "env").lower()


def get_secret(name: str, default: str | None = None) -> str | None:
    """Return the value of a named secret.

    Args:
        name: Secret name (e.g. ``"SECRET_KEY"``).
        default: Value returned if the secret is missing.

    Returns:
        The secret value or ``default``.

    Raises:
        NotImplementedError: If a non-``env`` backend is selected but not
            yet implemented.
    """
    if _BACKEND == "env":
        return os.environ.get(name, default)

    if _BACKEND in {"aws", "gcp", "vault"}:
        raise NotImplementedError(
            f"SECRETS_BACKEND={_BACKEND} requires a provider implementation. "
            "Add it here — do not scatter provider calls through business code."
        )

    raise ValueError(f"Unknown SECRETS_BACKEND: {_BACKEND}")
