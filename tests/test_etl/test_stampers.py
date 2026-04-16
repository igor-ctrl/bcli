"""Tests for ETL stampers. Pure units — no dlt, no network."""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("dlt")

from bcli.etl._stampers import (
    apply_stampers,
    audit_stamper,
    company_id_stamper,
    fivetran_stamper,
)


class TestFivetranStamper:
    def test_adds_both_fields(self):
        stamp = fivetran_stamper()
        out = stamp([{"id": "1", "name": "Acme"}, {"id": "2", "name": "Globex"}])
        for record in out:
            assert "_fivetran_synced" in record
            assert record["_fivetran_deleted"] is False

    def test_preserves_existing_fields(self):
        stamp = fivetran_stamper()
        out = stamp([{"id": "1", "name": "Acme"}])
        assert out[0]["id"] == "1"
        assert out[0]["name"] == "Acme"

    def test_does_not_mutate_input(self):
        stamp = fivetran_stamper()
        src = [{"id": "1"}]
        stamp(src)
        assert "_fivetran_synced" not in src[0]

    def test_synced_is_iso_timestamp(self):
        stamp = fivetran_stamper()
        out = stamp([{"id": "1"}])
        # Parseable as ISO timestamp
        datetime.fromisoformat(out[0]["_fivetran_synced"])


class TestAuditStamper:
    def test_adds_synced_at_and_source(self):
        stamp = audit_stamper("test-source")
        out = stamp([{"id": "1"}])
        assert "_synced_at" in out[0]
        assert out[0]["_source"] == "test-source"


class TestCompanyIdStamper:
    def test_injects_company_id(self):
        stamp = company_id_stamper("co-123")
        out = stamp([{"id": "1"}, {"id": "2"}])
        for record in out:
            assert record["company_id"] == "co-123"


class TestApplyStampers:
    def test_applies_in_order(self):
        stampers = [company_id_stamper("co-1"), audit_stamper("src-1")]
        out = apply_stampers([{"id": "1"}], stampers)
        assert out[0]["company_id"] == "co-1"
        assert out[0]["_source"] == "src-1"
        assert out[0]["id"] == "1"

    def test_empty_list_is_noop(self):
        out = apply_stampers([{"id": "1"}], [])
        assert out == [{"id": "1"}]
