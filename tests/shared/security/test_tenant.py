"""Unit tests for tenant isolation helpers."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Column, String
from sqlalchemy.orm import declarative_base

from src.shared.security.tenant import (
    TenantScopeViolation,
    assert_same_institution,
    tenant_scoped_query,
)

_Base = declarative_base()


class _Scoped(_Base):
    __tablename__ = "_scoped_test_table"
    id = Column(String, primary_key=True)
    institution_id = Column(String, nullable=False)


class _Unscoped(_Base):
    __tablename__ = "_unscoped_test_table"
    id = Column(String, primary_key=True)


class TestTenantScopedQuery:
    def test_filter_compiled_into_where(self) -> None:
        inst_id = uuid.uuid4()
        stmt = tenant_scoped_query(_Scoped, inst_id)
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        # Some dialects render UUIDs without dashes — compare hex chars only.
        assert inst_id.hex in compiled.replace("-", "")
        assert "institution_id" in compiled

    def test_unscoped_model_rejected(self) -> None:
        with pytest.raises(TypeError, match="not tenant-scoped"):
            tenant_scoped_query(_Unscoped, uuid.uuid4())


class TestAssertSameInstitution:
    def test_matching_uuids_pass(self) -> None:
        inst = uuid.uuid4()
        # No exception means pass.
        assert_same_institution(inst, inst)

    def test_matching_strings_pass(self) -> None:
        s = str(uuid.uuid4())
        assert_same_institution(s, s)

    def test_mismatch_raises(self) -> None:
        with pytest.raises(TenantScopeViolation):
            assert_same_institution(uuid.uuid4(), uuid.uuid4())

    def test_none_resource_id_raises(self) -> None:
        with pytest.raises(TenantScopeViolation, match="no institution_id"):
            assert_same_institution(None, uuid.uuid4())

    def test_string_uuid_against_uuid_passes(self) -> None:
        u = uuid.uuid4()
        assert_same_institution(str(u), u)
