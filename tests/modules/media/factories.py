"""Factory-boy factories for media module tests.

Only one table today (`media_files`); the factory is a thin wrapper around
`MediaFile(**...)` with sensible defaults. Callers must pass `institution_id`
and `uploader_id` because those are tenant/user references we don't invent.
"""

from __future__ import annotations

import uuid
from typing import Any

import factory

from src.modules.media.models.db_models import MediaFile


class MediaFileFactory(factory.Factory):  # type: ignore[misc]
    """A MediaFile in `pending` state with image/jpeg defaults.

    Override any field at call time, e.g.::

        media = MediaFileFactory(
            institution_id=str(inst.id),
            uploader_id=str(user.id),
            upload_status="completed",
        )
    """

    class Meta:
        model = MediaFile

    id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    institution_id: Any = None  # set by caller — no sensible default
    uploader_id: Any = None  # set by caller — no sensible default
    file_name = factory.Sequence(lambda n: f"photo_{n}.jpg")
    file_size = 123456
    mime_type = "image/jpeg"
    storage_key = factory.LazyAttribute(
        lambda o: f"{o.institution_id}/originals/2026/04/{o.id}.jpg"
    )
    upload_status = "pending"
