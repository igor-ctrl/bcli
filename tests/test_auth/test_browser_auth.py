"""Regression tests for vuln-0003 — browser auth callback DoS.

Before the fix, ``BrowserAuth.get_access_token()``:

  1. bound a fixed loopback port (8400), so any local process holding
     that port pre-authentication caused immediate failure;
  2. served exactly one request, so any stray GET (e.g. /favicon.ico
     from the browser, an unrelated probe) consumed the only callback
     slot and the legitimate OAuth callback got dropped.

The fix uses an ephemeral kernel-assigned port and keeps the listener
running until either a state-bound callback arrives or the timeout
expires. These tests run the real listener with a stubbed MSAL client to
prove both DoS paths are closed.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

import bcli.auth._browser as browser_mod
from bcli.auth._browser import BrowserAuth
from bcli.errors import AuthError


# ── Test doubles ──────────────────────────────────────────────────────────


class _DummyCache:
    def __init__(self) -> None:
        self.cached: list[tuple[str, str, str, int]] = []

    def get(self, tenant_id: str, client_id: str) -> None:
        return None

    def put(self, tenant_id: str, client_id: str, access_token: str, expires_in: int) -> None:
        self.cached.append((tenant_id, client_id, access_token, expires_in))

    def clear(self, *args: object, **kwargs: object) -> None:
        pass


class _FakeMSALApp:
    """Stub MSAL public client that mirrors the behaviour the code uses."""

    last_redirect_uri: str | None = None

    def __init__(
        self,
        client_id: str | None = None,
        authority: str | None = None,
        token_cache: object | None = None,
        **kwargs: object,
    ) -> None:
        self.client_id = client_id
        self.authority = authority
        # BrowserAuth now hands MSAL a persistent cache so refresh tokens
        # survive process exit. Capture it so a test can assert it was passed.
        self.token_cache = token_cache

    def get_accounts(self) -> list:
        return []

    def acquire_token_silent(self, scopes, account):
        return None

    def initiate_auth_code_flow(self, scopes, redirect_uri, **kwargs):
        # Capture the redirect URI so a test can assert ephemeral-port use.
        _FakeMSALApp.last_redirect_uri = redirect_uri
        return {
            "auth_uri": "http://example.invalid/auth?state=STATE123",
            "state": "STATE123",
            "code_verifier": "VERIFIER123",
            "redirect_uri": redirect_uri,
            "scopes": scopes,
        }

    def acquire_token_by_auth_code_flow(self, flow, auth_response):
        if auth_response.get("state") != flow.get("state"):
            return {"error": "state_mismatch", "error_description": "state mismatch"}
        if auth_response.get("code") != "GOODCODE":
            return {"error": "bad_code", "error_description": "bad code"}
        return {"access_token": "TOKEN123", "expires_in": 3600}


@pytest.fixture
def stub_msal(monkeypatch):
    """Replace ``msal.PublicClientApplication`` with the fake above."""
    _FakeMSALApp.last_redirect_uri = None
    monkeypatch.setattr(browser_mod.msal, "PublicClientApplication", _FakeMSALApp)
    monkeypatch.setattr(browser_mod, "_open_browser", lambda *a, **kw: None)
    # Short timeout so a missing/bad callback doesn't stall the suite.
    monkeypatch.setattr(browser_mod, "_AUTH_TIMEOUT", 4)
    yield


def _make_auth() -> BrowserAuth:
    # The MSAL cache path is redirected to tmp_path by the autouse
    # ``_isolate_msal_cache`` fixture in tests/conftest.py, so this never
    # touches the developer's real ~/.config/bcli/msal_cache.json.
    return BrowserAuth(
        tenant_id="tenant",
        client_id="client",
        token_cache=_DummyCache(),
    )


def _send_after(path: str, port: int, delay: float) -> threading.Thread:
    """Send a GET to the local listener after ``delay`` seconds."""

    def runner() -> None:
        time.sleep(delay)
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=2,
            )
        except (urllib.error.URLError, urllib.error.HTTPError):
            pass

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t


def _wait_for_redirect_uri(timeout: float = 3.0) -> str:
    """Spin until the auth thread populates the captured redirect URI."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        uri = _FakeMSALApp.last_redirect_uri
        if uri:
            return uri
        time.sleep(0.02)
    raise AssertionError("MSAL flow never initiated within timeout")


def _port_from_uri(uri: str) -> int:
    return int(uri.rsplit(":", 1)[-1])


def _run_auth_in_thread(auth: BrowserAuth) -> tuple[threading.Thread, dict, dict]:
    """Run ``auth.get_access_token()`` in a dedicated thread.

    BrowserAuth is declared async but does blocking I/O (HTTP listener,
    threading.Event.wait) — it would freeze the event loop if driven via
    asyncio.create_task. Running it in its own thread lets the test
    driver send callbacks from the main thread.
    """
    result: dict = {}
    err: dict = {}

    def runner() -> None:
        try:
            result["token"] = asyncio.run(auth.get_access_token())
        except Exception as exc:  # noqa: BLE001 — capture for assertions
            err["error"] = exc

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return t, result, err


# ── Tests ─────────────────────────────────────────────────────────────────


def test_default_port_is_ephemeral():
    """The hard-coded 8400 must be gone; ``_DEFAULT_PORT == 0`` asks the
    kernel to pick a free port at bind time.
    """
    assert browser_mod._DEFAULT_PORT == 0


def test_redirect_uri_uses_ephemeral_port(stub_msal):
    auth = _make_auth()
    thread, result, err = _run_auth_in_thread(auth)

    uri = _wait_for_redirect_uri()
    port = _port_from_uri(uri)
    assert port != 0
    # The bound port must NOT be the legacy fixed 8400 unless the kernel
    # genuinely happens to hand it back. The structural guarantee we
    # check above (port != 0) plus the prebind test below cover it.

    _send_after("/?code=GOODCODE&state=STATE123", port, 0.1)
    thread.join(timeout=10)

    assert "error" not in err, f"unexpected error: {err.get('error')!r}"
    assert result["token"] == "TOKEN123"


def test_port_prebind_no_longer_dos(stub_msal):
    """Pre-binding the legacy 8400 must not affect the new flow."""
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", 8400))
        sock.listen(1)
    except OSError:
        pytest.skip("Could not pre-bind 8400 in this environment")

    try:
        auth = _make_auth()
        thread, result, err = _run_auth_in_thread(auth)

        uri = _wait_for_redirect_uri()
        port = _port_from_uri(uri)
        # The new flow should have grabbed something else.
        assert port != 8400

        _send_after("/?code=GOODCODE&state=STATE123", port, 0.1)
        thread.join(timeout=10)

        assert "error" not in err, f"unexpected error: {err.get('error')!r}"
        assert result["token"] == "TOKEN123"
    finally:
        sock.close()


def test_stray_first_request_does_not_consume_callback(stub_msal):
    """A /favicon.ico (or any non-callback path) before the OAuth redirect
    must NOT short-circuit the listener.
    """
    auth = _make_auth()
    thread, result, err = _run_auth_in_thread(auth)

    uri = _wait_for_redirect_uri()
    port = _port_from_uri(uri)
    _send_after("/favicon.ico", port, 0.1)
    _send_after("/?code=GOODCODE&state=STATE123", port, 0.4)
    thread.join(timeout=10)

    assert "error" not in err, f"unexpected error: {err.get('error')!r}"
    assert result["token"] == "TOKEN123"


def test_bad_state_does_not_consume_callback(stub_msal):
    """A callback with the wrong state must be rejected without ending
    the listener — the legitimate callback that follows still wins.
    """
    auth = _make_auth()
    thread, result, err = _run_auth_in_thread(auth)

    uri = _wait_for_redirect_uri()
    port = _port_from_uri(uri)
    _send_after("/?code=GOODCODE&state=WRONGSTATE", port, 0.1)
    _send_after("/?code=GOODCODE&state=STATE123", port, 0.4)
    thread.join(timeout=10)

    assert "error" not in err, f"unexpected error: {err.get('error')!r}"
    assert result["token"] == "TOKEN123"


def test_timeout_when_no_valid_callback_arrives(stub_msal):
    """If the deadline elapses without a state-bound callback we surface
    an AuthError — the listener is no longer hostage to a stray request.
    """
    auth = _make_auth()
    thread, _result, err = _run_auth_in_thread(auth)

    uri = _wait_for_redirect_uri()
    port = _port_from_uri(uri)
    _send_after("/favicon.ico", port, 0.1)
    _send_after("/?code=GOODCODE&state=WRONG", port, 0.3)
    thread.join(timeout=10)

    assert isinstance(err.get("error"), AuthError)
    assert "timed out" in str(err["error"])
