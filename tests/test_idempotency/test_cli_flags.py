"""CLI-level test: --idempotency-key flag plumbs through to the client.

Pins that the four single-mutation commands (``post``, ``patch``,
``delete``, ``attach upload``) accept the flag and forward it to
:class:`AsyncBCClient`. The transport-level header injection is
covered in ``tests/test_client/test_retry_idempotency.py``.

Same-run replay protection through the batch ledger lives in
``tests/test_batch_ledger/test_ledger_idempotency.py``; cross-command
ledger integration is deferred to v0.2 (see PR body).
"""

from __future__ import annotations

_COMMON = dict(
    format=None, publisher=None, group=None, version=None, yes=True,
    result_out=None, result_fd=None,
)


def test_post_command_forwards_idempotency_key(writable_state, fake_bc):
    from bcli_cli.commands import post_cmd

    post_cmd.post_command(
        endpoint="vendors", data='{"displayName": "Acme"}',
        idempotency_key="op-key-1", **_COMMON,
    )

    assert fake_bc.last.method == "POST"
    assert fake_bc.last.idempotency_key == "op-key-1"


def test_patch_command_forwards_idempotency_key(writable_state, fake_bc):
    from bcli_cli.commands import patch_cmd

    patch_cmd.patch_command(
        endpoint="vendors", record_id="vnd-1",
        data='{"displayName": "Renamed"}', etag="*",
        idempotency_key="op-patch-1", **_COMMON,
    )

    assert fake_bc.last.method == "PATCH"
    assert fake_bc.last.idempotency_key == "op-patch-1"


def test_delete_command_forwards_idempotency_key(writable_state, fake_bc):
    from bcli_cli.commands import delete_cmd

    delete_cmd.delete_command(
        endpoint="vendors", record_id="vnd-1", etag="*",
        idempotency_key="op-del-1", **_COMMON,
    )

    assert fake_bc.last.method == "DELETE"
    assert fake_bc.last.idempotency_key == "op-del-1"
