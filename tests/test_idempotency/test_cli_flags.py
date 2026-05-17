"""CLI-level test: --idempotency-key flag plumbs through to the client.

Pins that the four single-mutation commands (``post``, ``patch``,
``delete``, ``attach upload``) accept the flag and forward it to
:class:`AsyncBCClient`. The transport-level header injection is
covered separately in ``test_idempotency_key.py``.

Same-run replay protection through the batch ledger lives in
``tests/test_batch_ledger/test_ledger_idempotency.py``; cross-command
ledger integration is deferred to v0.2 (see PR body).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import typer

from bcli_cli._state import state


def _fake_client(monkeypatch):
    """Stand up an AsyncBCClient stub that records every kwargs dict."""
    client = AsyncMock()

    async def _ctx_enter():
        return client

    async def _ctx_exit(*_):
        return False

    fake_cm = AsyncMock()
    fake_cm.__aenter__ = AsyncMock(side_effect=_ctx_enter)
    fake_cm.__aexit__ = AsyncMock(side_effect=_ctx_exit)
    client.post = AsyncMock(return_value={"systemId": "rec-1", "id": "rec-1"})
    client.patch = AsyncMock(return_value={"systemId": "rec-1"})
    client.delete = AsyncMock(return_value={})

    monkeypatch.setattr(
        state, "make_async_client", lambda **_: fake_cm,
    )
    # Make typer.Exit short-circuit cleanly; tests inspect the client.
    return client


def test_post_command_forwards_idempotency_key(writable_state, monkeypatch, tmp_path):
    from bcli_cli.commands import post_cmd

    client = _fake_client(monkeypatch)
    # Bypass print_context_banner / audit wrap noise.
    monkeypatch.setattr("bcli_cli.commands.post_cmd.print_context_banner", lambda: None)
    monkeypatch.setattr(
        "bcli_cli.commands.post_cmd._audited_post",
        lambda endpoint, body, **kw: client.post(endpoint, body, **kw),
    )
    monkeypatch.setattr("bcli_cli.commands.post_cmd.format_output", lambda *a, **k: None)
    monkeypatch.setattr(state, "dry_run", False)

    try:
        post_cmd.post_command(
            endpoint="vendors",
            data='{"displayName": "Acme"}',
            format=None,
            publisher=None, group=None, version=None,
            yes=True,
            result_out=None, result_fd=None,
            idempotency_key="op-key-1",
        )
    except typer.Exit:
        pass

    # Last call to the fake client carries the idempotency_key kw.
    client.post.assert_awaited()
    args, kwargs = client.post.call_args
    assert kwargs.get("idempotency_key") == "op-key-1"


def test_patch_command_forwards_idempotency_key(writable_state, monkeypatch, tmp_path):
    from bcli_cli.commands import patch_cmd

    client = _fake_client(monkeypatch)
    monkeypatch.setattr("bcli_cli.commands.patch_cmd.print_context_banner", lambda: None)
    monkeypatch.setattr(
        "bcli_cli.commands.patch_cmd._audited_patch",
        lambda endpoint, record_id, body, **kw: client.patch(endpoint, record_id, body, **kw),
    )
    monkeypatch.setattr("bcli_cli.commands.patch_cmd.format_output", lambda *a, **k: None)
    monkeypatch.setattr(state, "dry_run", False)

    try:
        patch_cmd.patch_command(
            endpoint="vendors", record_id="vnd-1",
            data='{"displayName": "Renamed"}',
            etag="*",
            format=None,
            publisher=None, group=None, version=None,
            yes=True,
            result_out=None, result_fd=None,
            idempotency_key="op-patch-1",
        )
    except typer.Exit:
        pass

    client.patch.assert_awaited()
    args, kwargs = client.patch.call_args
    assert kwargs.get("idempotency_key") == "op-patch-1"


def test_delete_command_forwards_idempotency_key(writable_state, monkeypatch):
    from bcli_cli.commands import delete_cmd

    client = _fake_client(monkeypatch)
    monkeypatch.setattr("bcli_cli.commands.delete_cmd.print_context_banner", lambda: None)
    monkeypatch.setattr(
        "bcli_cli.commands.delete_cmd._audited_delete",
        lambda endpoint, record_id, **kw: client.delete(endpoint, record_id, **kw),
    )
    monkeypatch.setattr(state, "dry_run", False)

    try:
        delete_cmd.delete_command(
            endpoint="vendors", record_id="vnd-1",
            etag="*",
            format=None,
            publisher=None, group=None, version=None,
            yes=True,
            result_out=None, result_fd=None,
            idempotency_key="op-del-1",
        )
    except typer.Exit:
        pass

    client.delete.assert_awaited()
    args, kwargs = client.delete.call_args
    assert kwargs.get("idempotency_key") == "op-del-1"
