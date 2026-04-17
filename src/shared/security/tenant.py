"""Tenant (institution) isolation helpers.

DuttaMessenger's multi-tenant boundary is the `institutions` row. A user from
institution A must never read, mutate, or even acknowledge existence of
institution B's data. App-layer enforcement lives here; PostgreSQL RLS is the
defence-in-depth layer and is applied per-table as needed.

Every service method that touches tenant-scoped data is expected to:
  1. Accept `institution_id` as a parameter (or read from the current user).
  2. Use `tenant_scoped_query(Model, institution_id)` to build queries.
  3. Assert via `assert_same_institution(...)` when a resource ID is passed
     in and must belong to the same tenant.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select


class TenantScopeViolation(Exception):
    """Raised when a cross-tenant access is detected.

    The route layer converts this into a 404 (not 403) so an attacker cannot
    distinguish between "resource does not exist" and "resource exists but
    belongs to another institution".
    """


def tenant_scoped_query(model: Any, institution_id: uuid.UUID) -> Select[Any]:
    """Start a `select(model).where(model.institution_id == institution_id)` chain.

    Every query against tenant-scoped tables MUST go through this helper so
    missing the filter is visible in code review.
    """
    if not hasattr(model, "institution_id"):
        raise TypeError(f"{model.__name__} is not tenant-scoped (no institution_id column)")
    return select(model).where(model.institution_id == institution_id)


def assert_same_institution(
    resource_institution_id: uuid.UUID | str | None,
    user_institution_id: uuid.UUID | str,
) -> None:
    """Raise `TenantScopeViolation` if the IDs don't match.

    `None` is treated as a violation too — callers must explicitly pass the
    resource's institution ID rather than relying on a nullable default.
    """
    if resource_institution_id is None:
        raise TenantScopeViolation("resource has no institution_id")
    if str(resource_institution_id) != str(user_institution_id):
        raise TenantScopeViolation(
            f"resource institution {resource_institution_id} != user institution {user_institution_id}"
        )
