"""Tests for WorkOS identity-cache TTL behaviour.

A revoked WorkOS role must not keep mapping to a privileged BC app forever.
``_resolve_bc_client_id`` re-validates by returning the default client_id
once the cache exceeds ``_WORKOS_CACHE_TTL_SECONDS``, which forces the
caller to run a fresh ``_workos_login()`` and re-fetch organisation
membership from WorkOS.
"""

from __future__ import annotations

import json
import time

from bcli.auth import _workos as workos_module
from bcli.auth._workos import WorkOSAuth, _WORKOS_CACHE_TTL_SECONDS


def _make_auth(monkeypatch, tmp_path):
    identity_file = tmp_path / "workos_identity.json"
    monkeypatch.setattr(workos_module, "_WORKOS_IDENTITY_FILE", identity_file)
    return WorkOSAuth(
        tenant_id="t",
        workos_api_key="k",
        workos_client_id="c",
        role_mapping={"admin": "admin-client-id", "member": "member-client-id"},
        default_bc_client_id="default-client-id",
    ), identity_file


def _write_identity(path, *, role: str, cached_at):
    payload = {
        "user_id": "u",
        "email": "u@example.com",
        "role": role,
        "bc_client_id": "ignored-by-resolver",
    }
    if cached_at is not None:
        payload["cached_at"] = cached_at
    path.write_text(json.dumps(payload))


def test_fresh_cache_returns_mapped_client_id(tmp_path, monkeypatch):
    auth, identity_file = _make_auth(monkeypatch, tmp_path)
    _write_identity(identity_file, role="admin", cached_at=time.time())
    assert auth._resolve_bc_client_id() == "admin-client-id"


def test_expired_cache_falls_back_to_default(tmp_path, monkeypatch):
    """Cache older than the TTL forces a re-login (default client_id)."""
    auth, identity_file = _make_auth(monkeypatch, tmp_path)
    expired = time.time() - _WORKOS_CACHE_TTL_SECONDS - 60
    _write_identity(identity_file, role="admin", cached_at=expired)
    assert auth._resolve_bc_client_id() == "default-client-id"


def test_cache_just_inside_ttl_is_trusted(tmp_path, monkeypatch):
    auth, identity_file = _make_auth(monkeypatch, tmp_path)
    just_inside = time.time() - (_WORKOS_CACHE_TTL_SECONDS - 60)
    _write_identity(identity_file, role="admin", cached_at=just_inside)
    assert auth._resolve_bc_client_id() == "admin-client-id"


def test_legacy_cache_without_cached_at_forces_revalidation(tmp_path, monkeypatch):
    """A pre-fix cache file (no `cached_at` field) is treated as expired.

    This is the upgrade path: a user who logged in before the TTL fix
    re-validates on next CLI run instead of trusting an indefinite role.
    """
    auth, identity_file = _make_auth(monkeypatch, tmp_path)
    _write_identity(identity_file, role="admin", cached_at=None)
    assert auth._resolve_bc_client_id() == "default-client-id"


def test_corrupt_cached_at_forces_revalidation(tmp_path, monkeypatch):
    auth, identity_file = _make_auth(monkeypatch, tmp_path)
    _write_identity(identity_file, role="admin", cached_at="yesterday")
    assert auth._resolve_bc_client_id() == "default-client-id"


def test_missing_cache_file_returns_default(tmp_path, monkeypatch):
    auth, _ = _make_auth(monkeypatch, tmp_path)
    assert auth._resolve_bc_client_id() == "default-client-id"


def test_unknown_role_in_fresh_cache_returns_default(tmp_path, monkeypatch):
    """A role that isn't in role_mapping falls through to default.

    (Same behaviour as before the TTL change — verifying it still holds.)
    """
    auth, identity_file = _make_auth(monkeypatch, tmp_path)
    _write_identity(identity_file, role="contractor", cached_at=time.time())
    assert auth._resolve_bc_client_id() == "default-client-id"
