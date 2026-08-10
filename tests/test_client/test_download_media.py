"""Tests for AsyncBCClient.get_media / BCTransport.download — media stream download.

Two requests per download: the record (through the normal resolver, so the
registry and ``disable_standard_api`` still apply) and then the media link the
record advertises. The interesting cases are all about *which* link gets
fetched and what is left on disk when the fetch goes wrong.
"""

from __future__ import annotations

import httpx
import pytest

from bcli.client._async import AsyncBCClient
from bcli.client._transport import BCTransport
from bcli.errors import BCLIError, NotFoundError

# A BC-origin media link with placeholder ids — the host has to be on the
# allowlist or assert_bc_origin rejects it before any request goes out.
COMPANY_ID = "00000000-0000-0000-0000-000000000001"
RECORD_ID = "00000000-0000-0000-0000-000000000002"
MEDIA_URL = (
    f"https://api.businesscentral.dynamics.com/v2.0/Sandbox/api/v2.0"
    f"/companies({COMPANY_ID})/incomingDocuments({RECORD_ID})/content"
)
PDF_BYTES = b"%PDF-1.4\n%fake pdf bytes for testing\n%%EOF\n"


class FakeAuth:
    async def get_access_token(self) -> str:
        return "fake-token"


def _client(max_retries: int = 0) -> AsyncBCClient:
    c = AsyncBCClient(
        tenant_id="test-tenant",
        client_id="test-client",
        client_secret="test-secret",
        environment="Sandbox",
        company_id=COMPANY_ID,
    )
    c._transport = BCTransport(FakeAuth(), timeout=5, max_retries=max_retries)
    return c


@pytest.fixture
def client() -> AsyncBCClient:
    return _client()


def _record(**annotations: str) -> dict:
    base = {"id": RECORD_ID, "description": "an incoming document"}
    base.update(annotations)
    return base


def _part_files(directory) -> list:
    return sorted(directory.glob("*.part"))


class TestHappyPath:
    async def test_downloads_the_single_advertised_media_stream(
        self, client, tmp_path, httpx_mock,
    ):
        httpx_mock.add_response(
            json=_record(**{"content@odata.mediaReadLink": MEDIA_URL}),
        )
        httpx_mock.add_response(
            content=PDF_BYTES, headers={"content-type": "application/pdf"},
        )

        dest = tmp_path / "invoice.pdf"
        result = await client.get_media("incomingDocuments", RECORD_ID, dest)

        assert dest.read_bytes() == PDF_BYTES
        assert result == {
            "path": str(dest),
            "bytes_written": len(PDF_BYTES),
            "media_field": "content",
            "content_type": "application/pdf",
            "media_fields_discovered": ["content"],
        }

        requests = httpx_mock.get_requests()
        assert len(requests) == 2
        # Record first, through the resolver — not the media link.
        assert requests[0].method == "GET"
        assert str(requests[0].url).endswith(f"/incomingDocuments({RECORD_ID})")
        # ...then the media link exactly as the record advertised it.
        assert requests[1].method == "GET"
        assert str(requests[1].url) == MEDIA_URL
        assert requests[1].headers["authorization"] == "Bearer fake-token"
        # The client default is application/json; a media stream is whatever
        # BC decides to send.
        assert requests[1].headers["accept"] == "*/*"

    async def test_no_select_on_the_record_read(self, client, tmp_path, httpx_mock):
        """$select strips the annotations the download depends on."""
        httpx_mock.add_response(
            json=_record(**{"content@odata.mediaReadLink": MEDIA_URL}),
        )
        httpx_mock.add_response(content=PDF_BYTES)

        await client.get_media("incomingDocuments", RECORD_ID, tmp_path / "x.pdf")

        assert "$select" not in str(httpx_mock.get_requests()[0].url)

    async def test_overwrites_an_existing_destination(self, client, tmp_path, httpx_mock):
        """The SDK replaces the file; refusing to is the CLI's policy, not this layer's."""
        httpx_mock.add_response(
            json=_record(**{"content@odata.mediaReadLink": MEDIA_URL}),
        )
        httpx_mock.add_response(content=PDF_BYTES)

        dest = tmp_path / "existing.pdf"
        dest.write_bytes(b"stale contents")

        await client.get_media("incomingDocuments", RECORD_ID, dest)

        assert dest.read_bytes() == PDF_BYTES

    def test_sync_wrapper_delegates(self, tmp_path, httpx_mock, monkeypatch):
        from bcli.client._sync import BCClient

        httpx_mock.add_response(
            json=_record(**{"content@odata.mediaReadLink": MEDIA_URL}),
        )
        httpx_mock.add_response(content=PDF_BYTES)

        sync = BCClient.__new__(BCClient)
        sync._async = _client()

        dest = tmp_path / "sync.pdf"
        result = sync.get_media("incomingDocuments", RECORD_ID, dest)

        assert result["bytes_written"] == len(PDF_BYTES)
        assert dest.read_bytes() == PDF_BYTES


class TestFieldResolution:
    async def test_zero_media_fields_names_the_endpoint_and_suggests_media(
        self, client, tmp_path, httpx_mock,
    ):
        httpx_mock.add_response(json=_record())

        with pytest.raises(BCLIError) as exc:
            await client.get_media("vendors", RECORD_ID, tmp_path / "nope.pdf")

        message = str(exc.value)
        assert "vendors" in message
        assert "--media" in message
        assert "bcli endpoint fields vendors" in message
        assert not (tmp_path / "nope.pdf").exists()
        assert len(httpx_mock.get_requests()) == 1

    async def test_two_media_fields_lists_both_candidates(
        self, client, tmp_path, httpx_mock,
    ):
        httpx_mock.add_response(json=_record(**{
            "content@odata.mediaReadLink": MEDIA_URL,
            "thumbnail@odata.mediaReadLink": MEDIA_URL + "Thumb",
        }))

        with pytest.raises(BCLIError) as exc:
            await client.get_media("incomingDocuments", RECORD_ID, tmp_path / "x.pdf")

        message = str(exc.value)
        assert "content" in message
        assert "thumbnail" in message
        assert "--media" in message
        # Nothing downloaded — the record read is the only request.
        assert len(httpx_mock.get_requests()) == 1

    async def test_explicit_media_field_wins_over_discovery(
        self, client, tmp_path, httpx_mock,
    ):
        httpx_mock.add_response(json=_record(**{
            "content@odata.mediaReadLink": MEDIA_URL,
            "thumbnail@odata.mediaReadLink": MEDIA_URL + "Thumb",
        }))
        httpx_mock.add_response(content=b"thumb")

        result = await client.get_media(
            "incomingDocuments", RECORD_ID, tmp_path / "t.png", media_field="thumbnail",
        )

        assert result["media_field"] == "thumbnail"
        assert str(httpx_mock.get_requests()[1].url) == MEDIA_URL + "Thumb"
        assert sorted(result["media_fields_discovered"]) == ["content", "thumbnail"]

    async def test_explicit_field_without_annotation_composes_sub_resource(
        self, client, tmp_path, httpx_mock,
    ):
        """Pages that serve a media property without annotating it still work."""
        httpx_mock.add_response(json=_record())
        httpx_mock.add_response(content=PDF_BYTES)

        result = await client.get_media(
            "incomingDocuments", RECORD_ID, tmp_path / "x.pdf", media_field="attachment",
        )

        record_url = str(httpx_mock.get_requests()[0].url)
        assert str(httpx_mock.get_requests()[1].url) == f"{record_url}/attachment"
        assert result["media_field"] == "attachment"
        assert result["media_fields_discovered"] == []

    async def test_traversal_in_media_field_is_rejected_before_any_http(
        self, client, tmp_path, httpx_mock,
    ):
        """A field name is a single path component — it can't retarget the URL."""
        httpx_mock.add_response(json=_record())

        with pytest.raises(ValueError, match="media_field"):
            await client.get_media(
                "incomingDocuments", RECORD_ID, tmp_path / "x.pdf",
                media_field="../../evil",
            )

        # Only the record read happened; no media request was ever composed.
        assert len(httpx_mock.get_requests()) == 1
        assert not list(tmp_path.iterdir())


class TestOriginGuard:
    async def test_off_origin_media_link_is_refused(self, client, tmp_path, httpx_mock):
        """A tampered mediaReadLink must not receive the bearer token."""
        evil = "https://attacker.example/leak"
        httpx_mock.add_response(json=_record(**{"content@odata.mediaReadLink": evil}))

        with pytest.raises(ValueError, match="off-origin"):
            await client.get_media("incomingDocuments", RECORD_ID, tmp_path / "x.pdf")

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert all("attacker.example" not in str(r.url) for r in requests)
        assert not list(tmp_path.iterdir())


class TestRetryAndCleanup:
    async def test_retry_after_partial_stream_writes_the_body_once(
        self, tmp_path, httpx_mock,
    ):
        """A retried attempt truncates first, so bytes can't be written twice."""
        client = _client(max_retries=2)
        httpx_mock.add_response(
            json=_record(**{"content@odata.mediaReadLink": MEDIA_URL}),
        )
        httpx_mock.add_response(status_code=503, json={"error": {"message": "busy"}})
        httpx_mock.add_response(content=PDF_BYTES)

        dest = tmp_path / "retried.pdf"
        result = await client.get_media("incomingDocuments", RECORD_ID, dest)

        assert dest.read_bytes() == PDF_BYTES
        assert result["bytes_written"] == len(PDF_BYTES)
        assert _part_files(tmp_path) == []

    async def test_retry_after_mid_stream_network_error(self, tmp_path, httpx_mock):
        client = _client(max_retries=2)
        httpx_mock.add_response(
            json=_record(**{"content@odata.mediaReadLink": MEDIA_URL}),
        )
        httpx_mock.add_exception(httpx.ReadTimeout("dropped"))
        httpx_mock.add_response(content=PDF_BYTES)

        dest = tmp_path / "flaky.pdf"
        await client.get_media("incomingDocuments", RECORD_ID, dest)

        assert dest.read_bytes() == PDF_BYTES
        assert _part_files(tmp_path) == []

    async def test_404_on_the_media_link_leaves_no_file_and_no_litter(
        self, client, tmp_path, httpx_mock,
    ):
        httpx_mock.add_response(
            json=_record(**{"content@odata.mediaReadLink": MEDIA_URL}),
        )
        httpx_mock.add_response(
            status_code=404, json={"error": {"message": "Media not found"}},
        )

        dest = tmp_path / "missing.pdf"
        with pytest.raises(NotFoundError, match="404"):
            await client.get_media("incomingDocuments", RECORD_ID, dest)

        assert not dest.exists()
        assert list(tmp_path.iterdir()) == []
