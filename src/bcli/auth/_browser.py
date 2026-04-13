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

from bcli.auth._token_cache import TokenCache
from bcli.config._defaults import BC_SCOPE, ENTRA_AUTHORITY_BASE
from bcli.errors import AuthError

logger = logging.getLogger(__name__)

_AUTH_TIMEOUT = 120  # seconds to wait for browser callback
_DEFAULT_PORT = 8400  # fixed port — register http://localhost:8400 in Entra ID


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
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._token_cache = token_cache or TokenCache()
        self._authority = f"{ENTRA_AUTHORITY_BASE}/{tenant_id}"
        self._login_hint = login_hint
        self._incognito = incognito

    async def get_access_token(self) -> str:
        """Get a valid access token, using cache or browser flow."""
        # Check disk cache first
        cached = self._token_cache.get(self._tenant_id, self._client_id)
        if cached:
            return cached

        # Build MSAL public client
        app = msal.PublicClientApplication(
            client_id=self._client_id,
            authority=self._authority,
        )

        # Try silent acquisition from MSAL in-memory cache
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(
                scopes=[BC_SCOPE],
                account=accounts[0],
            )
            if result and "access_token" in result:
                self._cache_token(result)
                return result["access_token"]

        # Start browser auth flow
        port = _DEFAULT_PORT
        redirect_uri = f"http://localhost:{port}"

        # MSAL handles PKCE automatically via initiate_auth_code_flow
        flow_kwargs: dict[str, str] = {}
        if self._login_hint:
            # Pre-fill the email and skip account picker (coming from WorkOS)
            flow_kwargs["login_hint"] = self._login_hint
        else:
            # Standalone browser auth — show account picker
            flow_kwargs["prompt"] = "select_account"

        flow = app.initiate_auth_code_flow(
            scopes=[BC_SCOPE],
            redirect_uri=redirect_uri,
            **flow_kwargs,
        )

        if "auth_uri" not in flow:
            raise AuthError(
                f"Failed to initiate browser auth: {flow.get('error_description', 'Unknown error')}",
                status_code=401,
            )

        # Start localhost server to catch the callback
        auth_response: dict[str, Any] = {}
        server_error: list[str] = []

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                # Flatten single-value params
                auth_response.update({k: v[0] if len(v) == 1 else v for k, v in params.items()})

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

            def log_message(self, format: str, *args: object) -> None:
                pass  # Suppress HTTP server logs

        server = HTTPServer(("127.0.0.1", port), CallbackHandler)
        server.timeout = _AUTH_TIMEOUT

        # Open browser
        auth_url = flow["auth_uri"]
        mode = " (incognito)" if self._incognito else ""
        print(f"\nOpening browser for authentication{mode}...", file=sys.stderr)
        print(f"If the browser doesn't open, visit:\n  {auth_url}\n", file=sys.stderr)
        _open_browser(auth_url, incognito=self._incognito)

        # Wait for single callback request
        server_thread = threading.Thread(target=server.handle_request, daemon=True)
        server_thread.start()
        server_thread.join(timeout=_AUTH_TIMEOUT)
        server.server_close()

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

        self._cache_token(result)
        logger.info("Acquired BC API token via browser auth flow")
        return result["access_token"]

    def _cache_token(self, result: dict) -> None:
        """Cache the token to disk."""
        access_token = result["access_token"]
        expires_in = result.get("expires_in", 3600)
        self._token_cache.put(self._tenant_id, self._client_id, access_token, expires_in)

    def clear_cache(self) -> None:
        """Clear cached tokens for this tenant/client."""
        self._token_cache.clear(self._tenant_id, self._client_id)
