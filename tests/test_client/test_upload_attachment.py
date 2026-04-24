"""Tests for AsyncBCClient.upload_attachment — two-phase documentAttachments upload."""

from __future__ import annotations

from pathlib import Path

import pytest

from bcli.client._async import AsyncBCClient
from bcli.client._transport import BCTransport
from bcli.errors import BCLIError


class FakeAuth:
    async def get_access_token(self) -> str:
        return "fake-token"


@pytest.fixture
def client() -> AsyncBCClient:
    """Programmatic AsyncBCClient with a FakeAuth-backed transport pre-wired."""
    c = AsyncBCClient(
        tenant_id="test-tenant",
        client_id="test-client",
        client_secret="test-secret",
        environment="Sandbox",
        company_id="11111111-1111-1111-1111-111111111111",
    )
    c._transport = BCTransport(FakeAuth(), timeout=5, max_retries=0)
    return c


@pytest.fixture
def pdf_bytes() -> bytes:
    return b"%PDF-1.4\n%fake pdf bytes for testing\n%%EOF\n"


@pytest.fixture
def pdf_file(tmp_path: Path, pdf_bytes: bytes) -> Path:
    path = tmp_path / "invoice.pdf"
    path.write_bytes(pdf_bytes)
    return path


def _add_two_phase_responses(
    httpx_mock,
    *,
    attach_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    verify_byte_size: int | None = None,
    verify_file_name: str = "invoice.pdf",
) -> str:
    """Queue POST 201 (metadata) + PATCH 204 (content) + GET 200 (verify).

    ``verify_byte_size=None`` simulates the buggy-AL-trigger case where BC
    returns byteSize=0 even after the content PATCH succeeded.
    """
    httpx_mock.add_response(
        status_code=201,
        json={
            "id": attach_id,
            "@odata.etag": 'W/"JzE5O2RnY3hjQzZkK0c3RDdONkJoZGVGPTs="',
            "parentType": "Purchase Invoice",
            "fileName": "invoice.pdf",
        },
    )
    httpx_mock.add_response(status_code=204)
    httpx_mock.add_response(
        status_code=200,
        json={
            "id": attach_id,
            "parentType": "Purchase Invoice",
            "parentId": "inv-1",
            "fileName": verify_file_name,
            "byteSize": verify_byte_size if verify_byte_size is not None else 0,
        },
    )
    return attach_id


class TestUploadAttachment:
    async def test_two_phase_post_then_patch(self, client, pdf_file, pdf_bytes, httpx_mock):
        attach_id = _add_two_phase_responses(httpx_mock)

        result = await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-1",
            file_path=pdf_file,
        )

        assert result["id"] == attach_id
        # Fixture's verify GET returns byteSize=0, so helper falls back to len(raw)
        assert result["byteSize"] == len(pdf_bytes)
        assert result["bytesUploaded"] == len(pdf_bytes)
        assert result["contentUploaded"] is True

        requests = httpx_mock.get_requests()
        # POST (metadata) + PATCH (content) + GET (verify)
        assert len(requests) == 3
        assert requests[2].method == "GET"
        assert "/attachmentContent" not in str(requests[2].url)

        # Phase 1: POST metadata
        post_req = requests[0]
        assert post_req.method == "POST"
        assert "/documentAttachments" in str(post_req.url)
        assert not str(post_req.url).endswith("/attachmentContent")
        import json
        body = json.loads(post_req.content)
        assert body == {
            "parentType": "Purchase Invoice",
            "parentId": "inv-1",
            "fileName": "invoice.pdf",
        }
        # Critically, NO attachmentContent in phase 1 (avoids stream double-read)
        assert "attachmentContent" not in body
        assert "byteSize" not in body

        # Phase 2: PATCH binary to /attachmentContent
        patch_req = requests[1]
        assert patch_req.method == "PATCH"
        assert str(patch_req.url).endswith(f"/documentAttachments({attach_id})/attachmentContent")
        assert patch_req.content == pdf_bytes
        # mimetypes.guess_type('invoice.pdf') → application/pdf
        assert patch_req.headers["content-type"] == "application/pdf"
        # ETag from phase 1 forwarded as If-Match
        assert patch_req.headers["if-match"].startswith('W/"')

    async def test_content_type_override(self, client, pdf_file, httpx_mock):
        _add_two_phase_responses(httpx_mock)

        await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-2",
            file_path=pdf_file,
            content_type="application/octet-stream",
        )

        patch_req = httpx_mock.get_requests()[1]
        assert patch_req.headers["content-type"] == "application/octet-stream"

    async def test_content_type_fallback_when_unknown_extension(self, client, tmp_path, httpx_mock):
        unknown = tmp_path / "no-extension-file"
        unknown.write_bytes(b"raw binary blob")
        _add_two_phase_responses(httpx_mock)

        await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-3",
            file_path=unknown,
        )

        patch_req = httpx_mock.get_requests()[1]
        assert patch_req.headers["content-type"] == "application/octet-stream"

    async def test_file_name_override(self, client, pdf_file, httpx_mock):
        _add_two_phase_responses(httpx_mock)

        await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-4",
            file_path=pdf_file,
            file_name="renamed.pdf",
        )

        import json
        post_body = json.loads(httpx_mock.get_requests()[0].content)
        assert post_body["fileName"] == "renamed.pdf"

    async def test_acme_v15_override_routes_all_phases(self, client, pdf_file, httpx_mock):
        """Explicit publisher/group/version override must apply to POST, PATCH, and verify GET."""
        _add_two_phase_responses(httpx_mock)

        await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-5",
            file_path=pdf_file,
            publisher="acme",
            group="finance",
            version="v1.5",
        )

        requests = httpx_mock.get_requests()
        for req in requests:
            assert "/api/acme/finance/v1.5/" in str(req.url), (
                f"Expected Acme v1.5 route on all phases, got: {req.url}"
            )
        # Phase ordering sanity
        assert requests[0].method == "POST"
        assert "/documentAttachments" in str(requests[0].url)
        assert requests[1].method == "PATCH"
        assert str(requests[1].url).endswith("/attachmentContent")
        assert requests[2].method == "GET"

    async def test_raises_when_post_missing_id(self, client, pdf_file, httpx_mock):
        httpx_mock.add_response(status_code=201, json={"fileName": "x.pdf"})  # no id

        with pytest.raises(BCLIError, match="did not return an id"):
            await client.upload_attachment(
                parent_type="Purchase Invoice",
                parent_id="inv-6",
                file_path=pdf_file,
            )

    async def test_accepts_systemid_field_from_post(self, client, pdf_file, httpx_mock):
        """If the POST response uses 'systemId' instead of 'id', still proceeds."""
        sysid = "00000000-1111-2222-3333-444444444444"
        httpx_mock.add_response(
            status_code=201,
            json={"systemId": sysid, "@odata.etag": 'W/"abc"'},
        )
        httpx_mock.add_response(status_code=204)
        httpx_mock.add_response(status_code=200, json={"id": sysid, "byteSize": 615})

        result = await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-7",
            file_path=pdf_file,
        )

        assert result["id"] == sysid

    async def test_byte_size_uses_bc_value_when_nonzero(self, client, pdf_file, pdf_bytes, httpx_mock):
        """When BC recomputes byteSize after the content PATCH, prefer BC's value."""
        _add_two_phase_responses(httpx_mock, verify_byte_size=len(pdf_bytes))

        result = await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-bs1",
            file_path=pdf_file,
        )

        assert result["byteSize"] == len(pdf_bytes)
        assert result["bytesUploaded"] == len(pdf_bytes)
        assert result["record"]["byteSize"] == len(pdf_bytes)

    async def test_byte_size_falls_back_when_bc_reports_zero(self, client, pdf_file, pdf_bytes, httpx_mock):
        """Custom AL pages that don't recompute byteSize report 0; helper must fall back."""
        _add_two_phase_responses(httpx_mock, verify_byte_size=0)

        result = await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-bs2",
            file_path=pdf_file,
        )

        # BC says 0, but we know we uploaded len(pdf_bytes) — surface that.
        assert result["byteSize"] == len(pdf_bytes)
        assert result["bytesUploaded"] == len(pdf_bytes)
        assert result["record"]["byteSize"] == 0

    async def test_verify_failure_does_not_fail_upload(self, client, pdf_file, pdf_bytes, httpx_mock):
        """If the verify GET 500s, the upload result still succeeds with uploaded bytes."""
        httpx_mock.add_response(
            status_code=201,
            json={"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "@odata.etag": 'W/"x"'},
        )
        httpx_mock.add_response(status_code=204)
        httpx_mock.add_response(status_code=500, json={"error": {"message": "flaky"}})

        result = await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-bs3",
            file_path=pdf_file,
        )

        assert result["contentUploaded"] is True
        assert result["byteSize"] == len(pdf_bytes)
        assert result["record"] is None

    async def test_accepts_string_path(self, client, pdf_file, httpx_mock):
        _add_two_phase_responses(httpx_mock)

        await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-8",
            file_path=str(pdf_file),
        )

        import json
        post_body = json.loads(httpx_mock.get_requests()[0].content)
        assert post_body["fileName"] == "invoice.pdf"

    async def test_force_standard_bypasses_registry(self, client, pdf_file, httpx_mock, monkeypatch):
        """force_standard=True must ignore any custom 'documentAttachments' registry entry."""
        # Seed the registry with a custom entry that would normally win
        from bcli.registry._schema import EndpointMetadata
        custom = EndpointMetadata.model_validate({
            "entity_set_name": "documentAttachments",
            "entity_name": "documentAttachment",
            "api_publisher": "acme",
            "api_group": "finance",
            "api_version": "v1.5",
            "supports": ["GET", "POST", "PATCH", "DELETE"],
            "key_field": "systemId",
        })
        client._registry._custom["documentattachments"] = custom

        _add_two_phase_responses(httpx_mock)

        await client.upload_attachment(
            parent_type="Purchase Invoice",
            parent_id="inv-9",
            file_path=pdf_file,
            force_standard=True,
        )

        for req in httpx_mock.get_requests():
            url = str(req.url)
            assert "/api/v2.0/" in url, f"Expected standard v2.0 route, got: {url}"
            assert "/api/acme/" not in url, f"Must NOT route through acme: {url}"

    async def test_force_standard_rejects_explicit_publisher(self, client, pdf_file):
        with pytest.raises(ValueError, match="cannot be combined"):
            await client.upload_attachment(
                parent_type="Purchase Invoice",
                parent_id="inv-10",
                file_path=pdf_file,
                force_standard=True,
                publisher="acme", group="finance", version="v1.5",
            )
