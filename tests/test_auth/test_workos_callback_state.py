"""Regression tests for vuln-0001 — WorkOS callback state validation.

Before the fix, ``WorkOSAuth._workos_login()`` started an HTTP listener on
``127.0.0.1:8401`` and trusted any callback that arrived during the login
window: there was no per-login state token, so an unsolicited request to
``http://127.0.0.1:8401/callback?code=FORGED_CODE&state=attacker`` was
exchanged for a role-bearing WorkOS identity and persisted to disk.

These tests run the real localhost listener with a stubbed WorkOS SDK and
assert that the callback handler now:

  * generates an unpredictable ``state`` and includes it in the
    authorization URL;
  * 404s any request whose path is not ``/callback``;
  * 400s a callback whose ``state`` does not match the expected token,
    surfacing it as an auth failure rather than a timeout;
  * accepts and proceeds when ``state`` matches.
"""

from __future__ import annotations

import sys
import threading
import time
import types
import urllib.error
import urllib.request
from typing import Any

import pytest

from bcli.auth import _workos as workos_module
from bcli.auth._workos import WorkOSAuth
from bcli.errors import AuthError


# ── Test doubles ──────────────────────────────────────────────────────────


class _FakeUser:
    id = "victim-local-user"
    email = "user@example.com"


class _FakeAuthResult:
    user = _FakeUser()


class _FakeMembership:
    status = "active"
    role = {"slug": "admin"}


class _FakeMemberships:
    data = [_FakeMembership()]


class _FakeUserManagement:
    def __init__(self) -> None:
        self.captured_state: str | None = None
        self.authenticate_calls: list[str] = []

    def get_authorization_url(self, **kwargs: Any) -> str:
        # Capture the state the caller passed so the test can replay it.
        self.captured_state = kwargs.get("state")
        return "https://example.invalid/workos/start"

    def authenticate_with_code(self, code: str) -> _FakeAuthResult:
        self.authenticate_calls.append(code)
        return _FakeAuthResult()

    def list_organization_memberships(self, user_id: str) -> _FakeMemberships:
        return _FakeMemberships()


class _FakeWorkOSClient:
    last_instance: "_FakeWorkOSClient | None" = None

    def __init__(self, api_key: str, client_id: str) -> None:
        self.user_management = _FakeUserManagement()
        _FakeWorkOSClient.last_instance = self


@pytest.fixture
def fake_workos(monkeypatch):
    """Install a fake `workos` module + a non-interactive browser opener."""
    _FakeWorkOSClient.last_instance = None
    fake_mod = types.ModuleType("workos")
    fake_mod.WorkOSClient = _FakeWorkOSClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "workos", fake_mod)
    monkeypatch.setattr(workos_module, "_open_browser", lambda *a, **kw: None)
    # Short timeout so a missing/bad callback doesn't stall the test suite.
    monkeypatch.setattr(workos_module, "_AUTH_TIMEOUT", 4)
    yield


@pytest.fixture
def isolated_identity_cache(monkeypatch, tmp_path):
    """Redirect the WorkOS identity cache to a tmp path."""
    cache = tmp_path / "workos_identity.json"
    monkeypatch.setattr(workos_module, "_WORKOS_IDENTITY_FILE", cache)
    yield cache


def _make_auth() -> WorkOSAuth:
    return WorkOSAuth(
        tenant_id="tenant-1",
        workos_api_key="api-key",
        workos_client_id="workos-client",
        role_mapping={"admin": "privileged-bc-client", "member": "standard-bc-client"},
        default_bc_client_id="standard-bc-client",
    )


def _run_login_in_thread(auth: WorkOSAuth) -> tuple[threading.Thread, dict, dict]:
    """Run `_workos_login()` in a daemon thread and capture result/error."""
    result: dict = {}
    err: dict = {}

    def target() -> None:
        try:
            result["value"] = auth._workos_login()
        except Exception as exc:  # noqa: BLE001 — we want to capture all
            err["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    # Give the HTTP listener a moment to bind.
    time.sleep(0.3)
    return t, result, err


# ── Tests ─────────────────────────────────────────────────────────────────


def test_authorization_url_includes_state(fake_workos, isolated_identity_cache):
    """The login flow must pass a state token to WorkOS."""
    auth = _make_auth()
    thread, _result, _err = _run_login_in_thread(auth)

    # We don't need a real callback — just hit the listener once with a
    # matching state so the thread exits cleanly. We pull the expected
    # state from the fake WorkOS SDK.
    fake = _FakeWorkOSClient.last_instance
    assert fake is not None, "WorkOS client should have been constructed"
    state = fake.user_management.captured_state
    assert state, "state must be passed to get_authorization_url"
    assert len(state) >= 32, "state must be high-entropy (token_urlsafe(32))"

    # Drain the listener so the daemon thread shuts down promptly.
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{workos_module._WORKOS_PORT}/callback"
            f"?code=ok&state={state}",
            timeout=2,
        )
    except urllib.error.URLError:
        pass
    thread.join(timeout=5)


def test_unsolicited_callback_is_rejected(fake_workos, isolated_identity_cache):
    """A callback with a wrong state must be rejected as an auth failure.

    Before the fix this was the core exploit: any local actor that could
    reach the loopback listener could inject an attacker-controlled code.
    """
    auth = _make_auth()
    thread, _result, err = _run_login_in_thread(auth)

    url = (
        f"http://127.0.0.1:{workos_module._WORKOS_PORT}/callback"
        "?code=FORGED_CODE&state=attacker"
    )
    try:
        urllib.request.urlopen(url, timeout=2)
    except urllib.error.HTTPError as http_err:
        # Expected: callback handler returns 400 on state mismatch.
        assert http_err.code == 400
    except urllib.error.URLError:
        # Listener may already have closed in some race conditions — that's
        # fine; the assertion below covers the security guarantee either way.
        pass

    thread.join(timeout=5)

    # The login call must have failed, not silently succeeded.
    assert "error" in err, "login must raise on rejected callback"
    assert isinstance(err["error"], AuthError)
    assert "Invalid WorkOS callback state" in str(err["error"])

    # And — critically — the forged code must NOT have been exchanged.
    fake = _FakeWorkOSClient.last_instance
    assert fake is not None
    assert fake.user_management.authenticate_calls == [], (
        "authenticate_with_code must not be called for an unsolicited callback"
    )

    # Identity cache must not be written.
    assert not isolated_identity_cache.exists()


def test_callback_with_wrong_path_is_404(fake_workos, isolated_identity_cache):
    """Only `/callback` is honoured; other paths must 404 without side effects."""
    auth = _make_auth()
    thread, _result, _err = _run_login_in_thread(auth)

    fake = _FakeWorkOSClient.last_instance
    assert fake is not None
    state = fake.user_management.captured_state
    assert state

    # Hit a non-callback path with the correct state — must 404.
    bad_url = (
        f"http://127.0.0.1:{workos_module._WORKOS_PORT}/admin"
        f"?code=ok&state={state}"
    )
    try:
        urllib.request.urlopen(bad_url, timeout=2)
    except urllib.error.HTTPError as http_err:
        assert http_err.code == 404
    except urllib.error.URLError:
        pass

    # The 404 path must not consume the listener — the legitimate callback
    # below should still be accepted. Drain so the thread exits cleanly.
    good_url = (
        f"http://127.0.0.1:{workos_module._WORKOS_PORT}/callback"
        f"?code=ok&state={state}"
    )
    try:
        urllib.request.urlopen(good_url, timeout=2)
    except urllib.error.URLError:
        pass

    thread.join(timeout=5)


def test_matching_state_is_accepted(fake_workos, isolated_identity_cache):
    """A callback whose state matches the per-login token must succeed."""
    auth = _make_auth()
    thread, result, err = _run_login_in_thread(auth)

    fake = _FakeWorkOSClient.last_instance
    assert fake is not None
    state = fake.user_management.captured_state
    assert state

    url = (
        f"http://127.0.0.1:{workos_module._WORKOS_PORT}/callback"
        f"?code=GOOD_CODE&state={state}"
    )
    try:
        urllib.request.urlopen(url, timeout=2)
    except urllib.error.URLError:
        pass

    thread.join(timeout=5)

    assert "error" not in err, f"login should succeed; got {err.get('error')!r}"
    bc_client_id, email = result["value"]
    assert bc_client_id == "privileged-bc-client"
    assert email == "user@example.com"
    assert fake.user_management.authenticate_calls == ["GOOD_CODE"]
