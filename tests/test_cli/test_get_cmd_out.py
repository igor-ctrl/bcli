"""Tests for ``bcli get --out`` — media-stream download instead of printed records.

``--out`` flips the verb into a different mode, so most of what is worth
testing is the refusal path: the flag combinations that mean the caller
expected records, and the destination checks that must happen before any
network round trip.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import typer

from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli_cli._state import state
from bcli_cli.commands import get_cmd

RECORD_ID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def cli_state():
    cfg = BCConfig(
        defaults=BCDefaults(profile="dev"),
        profiles={
            "dev": BCProfile(
                tenant_id="t1",
                environment="Sandbox",
                company_id="c-123",
            ),
        },
    )
    state._config = cfg
    state._registry = None
    state.profile_name = None
    state.env_override = None
    state.company_override = None
    state.format = "table"
    state.format_explicit = False
    state.dry_run = False
    state.quiet = True
    yield state
    state._config = None
    state._registry = None
    state.profile_name = None
    state.format_explicit = False
    state.dry_run = False


@pytest.fixture
def fake_client(monkeypatch):
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.get_media = AsyncMock(return_value={
        "path": "/tmp/invoice.pdf",
        "bytes_written": 1234,
        "media_field": "content",
        "content_type": "application/pdf",
        "media_fields_discovered": ["content"],
    })
    monkeypatch.setattr(state, "make_async_client", lambda **_: c)
    return c


def _run(*, endpoint="incomingDocuments", record_id=RECORD_ID, **kwargs):
    kwargs.setdefault("filter", None)
    kwargs.setdefault("select", None)
    kwargs.setdefault("expand", None)
    kwargs.setdefault("orderby", None)
    kwargs.setdefault("top", None)
    kwargs.setdefault("skip", None)
    kwargs.setdefault("count", False)
    kwargs.setdefault("all_pages", False)
    kwargs.setdefault("out", None)
    kwargs.setdefault("media", None)
    kwargs.setdefault("overwrite", False)
    kwargs.setdefault("format", None)
    kwargs.setdefault("publisher", None)
    kwargs.setdefault("group", None)
    kwargs.setdefault("version", None)
    return get_cmd.get_command(endpoint=endpoint, record_id=record_id, **kwargs)


class TestForwarding:
    def test_forwards_every_argument_to_get_media(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        dest = tmp_path / "invoice.pdf"
        _run(
            out=dest, media="content",
            publisher="acme", group="finance", version="v1.5",
        )

        args = fake_client.get_media.await_args
        assert args.args[0] == "incomingDocuments"
        assert args.args[1] == RECORD_ID
        assert args.args[2] == dest
        assert args.kwargs["media_field"] == "content"
        assert args.kwargs["publisher"] == "acme"
        assert args.kwargs["group"] == "finance"
        assert args.kwargs["version"] == "v1.5"

    def test_expanduser_applied_before_the_client_sees_the_path(
        self, cli_state, fake_client, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        _run(out=Path("~/invoice.pdf"))

        assert fake_client.get_media.await_args.args[2] == tmp_path / "invoice.pdf"


class TestFlagValidation:
    def test_media_without_out_is_rejected(self, cli_state, fake_client):
        with pytest.raises(typer.BadParameter, match="--media requires --out"):
            _run(media="content")
        assert fake_client.get_media.await_count == 0

    def test_out_without_record_id_is_rejected(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        with pytest.raises(typer.BadParameter, match="record id"):
            _run(record_id=None, out=tmp_path / "x.pdf")
        assert fake_client.get_media.await_count == 0

    @pytest.mark.parametrize("flag,value", [
        ("filter", "number eq '1'"),
        ("select", "number"),
        ("expand", "lines"),
        ("orderby", "number desc"),
        ("top", 5),
        ("skip", 5),
        ("count", True),
        ("all_pages", True),
        ("format", "json"),
    ])
    def test_query_shaping_flags_conflict_with_out(
        self, cli_state, fake_client, tmp_path: Path, flag, value,
    ):
        with pytest.raises(typer.BadParameter):
            _run(out=tmp_path / "x.pdf", **{flag: value})
        assert fake_client.get_media.await_count == 0

    def test_global_default_format_is_ignored_not_an_error(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        """A format inherited from config shapes printed records; --out prints none."""
        cli_state.format = "json"
        cli_state.format_explicit = True

        _run(out=tmp_path / "x.pdf")

        assert fake_client.get_media.await_count == 1


class TestDestinationChecks:
    def test_existing_file_refused_without_overwrite(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        dest = tmp_path / "invoice.pdf"
        dest.write_bytes(b"do not clobber me")

        with pytest.raises(typer.Exit) as exc:
            _run(out=dest)

        assert exc.value.exit_code == 1
        assert fake_client.get_media.await_count == 0
        assert dest.read_bytes() == b"do not clobber me"

    def test_existing_file_accepted_with_overwrite(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        dest = tmp_path / "invoice.pdf"
        dest.write_bytes(b"stale")

        _run(out=dest, overwrite=True)

        assert fake_client.get_media.await_count == 1

    def test_missing_parent_directory_is_an_error_not_an_mkdir(
        self, cli_state, fake_client, tmp_path: Path,
    ):
        dest = tmp_path / "no-such-dir" / "invoice.pdf"

        with pytest.raises(typer.Exit) as exc:
            _run(out=dest)

        assert exc.value.exit_code == 1
        assert fake_client.get_media.await_count == 0
        assert not dest.parent.exists()


class TestDryRun:
    def test_dry_run_touches_nothing(self, cli_state, fake_client, tmp_path: Path):
        cli_state.dry_run = True
        dest = tmp_path / "invoice.pdf"

        with pytest.raises(typer.Exit) as exc:
            _run(out=dest)

        assert (exc.value.exit_code or 0) == 0
        assert fake_client.get_media.await_count == 0
        assert not dest.exists()
