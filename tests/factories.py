"""Factory-boy factories for test data.

Each module adds its factories here as it's built. Factories produce valid
domain objects with sensible defaults so tests focus on behavior, not setup.
"""

from __future__ import annotations

import uuid
from typing import Any

import factory
from faker import Faker

fake = Faker()


class InstitutionFactory(factory.Factory):  # type: ignore[misc]
    """Institution with a unique name and domain."""

    class Meta:
        model = dict  # Replaced with actual model when auth module is wired.

    id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    name = factory.Sequence(lambda n: f"Test Institution {n}")
    domain = factory.Sequence(lambda n: f"inst{n}.test")
    subscription_tier = "free"
    max_users = 5000
    max_groups = 500


class UserFactory(factory.Factory):  # type: ignore[misc]
    """User bound to an Institution."""

    class Meta:
        model = dict

    id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    institution_id = factory.SubFactory(InstitutionFactory)
    email = factory.LazyAttribute(lambda _: fake.unique.email())
    full_name = factory.LazyAttribute(lambda _: fake.name())
    password_hash = "$2b$12$dummy.bcrypt.hash.used.in.tests.only"
    is_active = True


def rehydrate(data: dict[str, Any], model: type) -> Any:
    """Instantiate a SQLAlchemy model from a factory-produced dict.

    Kept as a helper so each module can wire its own model class without
    forcing factory_boy's SQLAlchemy session integration (which complicates
    the async session pattern used in tests).
    """
    return model(**data)
