"""Security primitives shared across modules.

- `rate_limit`: slowapi-backed per-IP / per-user request throttling.
- `tenant`: helpers that enforce `institution_id` scoping so a user from
  institution A can never read or mutate institution B's data.
- `secrets_provider`: abstraction that reads secrets from env today and can
  be swapped for Vault / AWS Secrets Manager / GCP Secret Manager without
  touching business code.
- `audit`: writes rows to the `audit_logs` table on every mutation.
"""

from src.shared.security.audit import AuditEvent, write_audit
from src.shared.security.rate_limit import limiter, limiter_exception_handler
from src.shared.security.secrets_provider import get_secret
from src.shared.security.tenant import (
    TenantScopeViolation,
    assert_same_institution,
    tenant_scoped_query,
)

__all__ = [
    "AuditEvent",
    "TenantScopeViolation",
    "assert_same_institution",
    "get_secret",
    "limiter",
    "limiter_exception_handler",
    "tenant_scoped_query",
    "write_audit",
]
