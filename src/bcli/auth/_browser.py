"""Authorization code flow with PKCE for interactive browser-based auth.

Opens the user's default browser to authenticate. A temporary localhost server
catches the redirect callback. The token carries the user's identity, so
Business Central enforces their permission sets on every API call.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import msal

from bcli.auth._msal_cache import MsalTokenCache
from bcli.auth._token_cache import TokenCache
from bcli.config._defaults import BC_SCOPE, ENTRA_AUTHORITY_BASE
from bcli.errors import AuthError

logger = logging.getLogger(__name__)

_AUTH_TIMEOUT = 120  # seconds to wait for browser callback
# Bind an ephemeral loopback port at runtime instead of a hard-coded one
# (vuln-0003). Microsoft Entra treats ``http://localhost`` redirect URIs as
# port-agnostic for public/native clients per RFC 8252 §7.3, so existing
# Entra app registrations of ``http://localhost`` or ``http://localhost:8400``
# continue to validate against ``http://localhost:<ephemeral>``. Pinning a
# single port let any local process (or stray request to e.g. /favicon.ico)
# either pre-bind 8400 or consume the only callback slot, denying service to
# the legitimate login flow.
_DEFAULT_PORT = 0  # 0 → kernel-assigned ephemeral port


def _open_browser(url: str, *, incognito: bool = False) -> None:
    """Open a URL in the browser, optionally in incognito/private mode."""
    if not incognito:
        webbrowser.open(url)
        return

    system = platform.system()
    try:
        if system == "Darwin":
            # Try Chrome first, fall back to Safari private
            for app, flag in [
                ("/Applications/Google Chrome.app", "--incognito"),
                ("/Applications/Microsoft Edge.app", "--inprivate"),
                ("/Applications/Brave Browser.app", "--incognito"),
            ]:
                try:
                    subprocess.Popen([
                        "open", "-na", app, "--args", flag, url,
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except (FileNotFoundError, OSError):
                    continue
            # Safari doesn't support incognito via CLI, fall back to default
            webbrowser.open(url)
        elif system == "Windows":
            for exe, flag in [
                ("chrome", "--incognito"),
                ("msedge", "--inprivate"),
            ]:
                try:
                    subprocess.Popen([exe, flag, url])
                    return
                except FileNotFoundError:
                    continue
            webbrowser.open(url)
        else:
            # Linux
            for exe, flag in [
                ("google-chrome", "--incognito"),
                ("chromium-browser", "--incognito"),
                ("firefox", "--private-window"),
            ]:
                try:
                    subprocess.Popen([exe, flag, url])
                    return
                except FileNotFoundError:
                    continue
            webbrowser.open(url)
    except Exception:
        webbrowser.open(url)


class BrowserAuth:
    """Interactive browser-based auth via authorization code flow with PKCE.

    User authenticates as themselves — BC enforces their permission sets.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        token_cache: TokenCache | None = None,
        login_hint: str | None = None,
        incognito: bool = False,
        msal_cache: MsalTokenCache | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._token_cache = token_cache or TokenCache()
        self._msal_cache = msal_cache or MsalTokenCache()
        self._authority = f"{ENTRA_AUTHORITY_BASE}/{tenant_id}"
        self._login_hint = login_hint
        self._incognito = incognito

    async def get_access_token(self) -> str:
        """Get a valid access token, using cache or browser flow."""
        # Check disk cache first
        cached = self._token_cache.get(self._tenant_id, self._client_id)
        if cached:
            return cached

        # Build MSAL public client. Passing token_cache is what lets the silent
        # path below survive process exit: MSAL's default cache is in-memory,
        # so without this get_accounts() is always empty in a fresh process and
        # the user gets a browser prompt every time the ~1h access token dies.
        app = msal.PublicClientApplication(
            client_id=self._client_id,
            authority=self._authority,
            token_cache=self._msal_cache.cache,
        )

        # Try silent acquisition — backed by the refresh token persisted by a
        # previous invocation, not merely this process's memory.
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(
                scopes=[BC_SCOPE],
                account=accounts[0],
            )
            if result and "access_token" in result:
                # A silent refresh usually rotates the refresh token; persist.
                self._msal_cache.save()
                self._cache_token(result)
                logger.info("Renewed BC API token silently (no browser)")
                return result["access_token"]

        # Start localhost server on an ephemeral port BEFORE generating the
        # auth URL — we need the actual bound port to embed in the
        # ``redirect_uri`` so the browser callback lands on this listener.
        # See vuln-0003: a hard-coded port can be pre-bound by another
        # process, denying service to the login flow.
        auth_response: dict[str, Any] = {}
        server_error: list[str] = []
        valid_callback = threading.Event()
        # ``flow_state`` is captured by the handler to validate ``state``
        # before signalling completion; we initialise the variable now and
        # set it after MSAL produces the flow dict.
        flow_state_holder: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                # Only the root path is the OAuth redirect target. Anything
                # else (e.g. a stray /favicon.ico from the browser, a probe
                # from another local process) gets a 404 without consuming
                # the callback slot — vuln-0003 rejected this case by
                # silently shutting down on the first request.
                if parsed.path not in ("/", ""):
                    self.send_response(404)
                    self.end_headers()
                    return

                params = parse_qs(parsed.query)
                state_param = params.get("state", [""])[0]
                expected_state = flow_state_holder.get("state", "")

                # Reject callbacks whose state doesn't match the per-flow
                # token. MSAL would also reject these at the exchange step,
                # but rejecting here keeps the listener live so the
                # legitimate callback can still be processed.
                if not expected_state or state_param != expected_state:
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h2>Unexpected callback</h2>"
                        b"<p>You can close this tab.</p></body></html>"
                    )
                    return

                # Valid (or at least state-bound) callback — flatten params,
                # send the success page, and signal the main thread.
                auth_response.update(
                    {k: v[0] if len(v) == 1 else v for k, v in params.items()}
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                if "error" in params:
                    error_msg = params.get("error_description", params.get("error", ["Unknown"]))[0]
                    server_error.append(error_msg)
                    self.wfile.write(
                        b"<html><body><h2>Authentication failed</h2>"
                        b"<p>You can close this tab.</p></body></html>"
                    )
                else:
                    self.wfile.write(
                        b"<html><body><h2>Authenticated successfully</h2>"
                        b"<p>You can close this tab and return to the terminal.</p></body></html>"
                    )
                valid_callback.set()

            def log_message(self, format: str, *args: object) -> None:
                pass  # Suppress HTTP server logs

        try:
            server = HTTPServer(("127.0.0.1", _DEFAULT_PORT), CallbackHandler)
        except OSError as exc:
            raise AuthError(
                f"Failed to bind localhost callback server: {exc}. "
                "Try 'bcli auth login --method device' as a fallback.",
                status_code=401,
            ) from exc

        # Bound port is whatever the kernel handed us when port=0.
        actual_port = server.server_address[1]
        redirect_uri = f"http://localhost:{actual_port}"

        # MSAL handles PKCE automatically via initiate_auth_code_flow
        flow_kwargs: dict[str, str] = {}
        if self._login_hint:
            # Pre-fill the email and skip the account picker when callers know it.
            flow_kwargs["login_hint"] = self._login_hint
        else:
            # Standalone browser auth — show account picker
            flow_kwargs["prompt"] = "select_account"

        try:
            flow = app.initiate_auth_code_flow(
                scopes=[BC_SCOPE],
                redirect_uri=redirect_uri,
                **flow_kwargs,
            )
        except Exception:
            server.server_close()
            raise

        if "auth_uri" not in flow:
            server.server_close()
            raise AuthError(
                f"Failed to initiate browser auth: {flow.get('error_description', 'Unknown error')}",
                status_code=401,
            )

        # Hand the per-flow state to the callback handler. MSAL embeds this
        # value in the auth_uri it returns.
        flow_state_holder["state"] = flow.get("state", "")

        # Open browser
        auth_url = flow["auth_uri"]
        mode = " (incognito)" if self._incognito else ""
        print(f"\nOpening browser for authentication{mode}...", file=sys.stderr)
        print(f"If the browser doesn't open, visit:\n  {auth_url}\n", file=sys.stderr)
        _open_browser(auth_url, incognito=self._incognito)

        # Serve continuously until either a valid callback fires the event
        # or the deadline elapses. ``handle_request`` once-and-done was the
        # vuln-0003 root cause: any stray request consumed the slot, even
        # if it wasn't the OAuth callback.
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            valid_callback.wait(timeout=_AUTH_TIMEOUT)
        finally:
            server.shutdown()
            server.server_close()
            # serve_forever returns once shutdown completes; give the thread
            # a brief join so it tears down cleanly.
            server_thread.join(timeout=2)

        if not auth_response:
            raise AuthError(
                f"Browser authentication timed out after {_AUTH_TIMEOUT} seconds. "
                "Try 'bcli auth login --method device' as a fallback.",
                status_code=401,
            )

        if server_error:
            raise AuthError(
                f"Browser authentication failed: {server_error[0]}",
                status_code=401,
            )

        # Exchange auth code for token
        result = app.acquire_token_by_auth_code_flow(flow, auth_response)

        if "access_token" not in result:
            error_desc = result.get("error_description", result.get("error", "Unknown error"))
            raise AuthError(f"Token acquisition failed: {error_desc}", status_code=401)

        self._msal_cache.save()
        self._cache_token(result)
        logger.info("Acquired BC API token via browser auth flow")
        return result["access_token"]

    def _cache_token(self, result: dict) -> None:
        """Cache the token to disk."""
        access_token = result["access_token"]
        expires_in = result.get("expires_in", 3600)
        self._token_cache.put(self._tenant_id, self._client_id, access_token, expires_in)

    def clear_cache(self) -> None:
        """Clear cached tokens for this tenant/client.

        Clears the persisted MSAL cache too. Dropping only the access token
        would leave the refresh token on disk, so a "logged out" user could
        still renew silently.
        """
        self._token_cache.clear(self._tenant_id, self._client_id)
        self._msal_cache.remove_accounts(
            client_id=self._client_id, authority=self._authority
        )
