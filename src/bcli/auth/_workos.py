"""WorkOS AuthKit integration for role-based BC client_id selection.

Flow:
1. User authenticates via WorkOS AuthKit (browser redirect)
2. WorkOS returns user identity + organization memberships
3. bcli maps the user's role to a BC Entra app client_id
4. BC browser auth runs with the selected client_id
5. BC enforces that app's permission sets on every API call

This gives you SSO + role-based BC access in one login flow.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from bcli.auth._browser import BrowserAuth
from bcli.auth._token_cache import TokenCache
from bcli.config._defaults import CONFIG_DIR
from bcli.errors import AuthError

logger = logging.getLogger(__name__)

_WORKOS_PORT = 8401  # separate from BC auth port (8400)
_WORKOS_REDIRECT_URI = f"http://localhost:{_WORKOS_PORT}/callback"
_AUTH_TIMEOUT = 120
_WORKOS_IDENTITY_FILE = CONFIG_DIR / "workos_identity.json"


class WorkOSAuth:
    """Two-step auth: WorkOS identity → role-based BC client_id → BC browser auth.

    Config (in config.toml):
        [workos]
        api_key = "sk_live_..."
        client_id = "client_..."

        [workos.groups]
        admin = { roles = ["admin"], bc_client_id = "7c25b4eb-..." }
        readonly = { roles = ["member"], bc_client_id = "6db881e3-..." }
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        workos_api_key: str,
        workos_client_id: str,
        role_mapping: dict[str, str],
        default_bc_client_id: str,
        token_cache: TokenCache | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._workos_api_key = workos_api_key
        self._workos_client_id = workos_client_id
        self._role_mapping = role_mapping  # {role_slug: bc_client_id}
        self._default_bc_client_id = default_bc_client_id
        self._token_cache = token_cache or TokenCache()
        self._bc_auth: BrowserAuth | None = None

    async def get_access_token(self) -> str:
        """Get a BC access token via WorkOS-gated browser auth."""
        # Check if we have a cached BC token
        bc_client_id = self._resolve_bc_client_id()
        cached = self._token_cache.get(self._tenant_id, bc_client_id)
        if cached:
            return cached

        # Need to authenticate — determine BC client_id from WorkOS identity
        if bc_client_id == self._default_bc_client_id:
            # No cached WorkOS identity — do the full WorkOS flow
            bc_client_id = self._workos_login()

        # Now do BC browser auth with the resolved client_id
        self._bc_auth = BrowserAuth(
            tenant_id=self._tenant_id,
            client_id=bc_client_id,
            token_cache=self._token_cache,
        )
        return await self._bc_auth.get_access_token()

    def _resolve_bc_client_id(self) -> str:
        """Check cached WorkOS identity for role → client_id mapping."""
        identity = _load_workos_identity()
        if identity:
            role = identity.get("role", "")
            bc_client_id = self._role_mapping.get(role)
            if bc_client_id:
                logger.debug("WorkOS cached identity: role=%s → client_id=%s", role, bc_client_id[:8])
                return bc_client_id
        return self._default_bc_client_id

    def _workos_login(self) -> str:
        """Run WorkOS browser auth, get user role, return BC client_id."""
        try:
            from workos import WorkOSClient as WorkOS
        except ImportError as e:
            raise AuthError(
                f"WorkOS SDK not available: {e}. Run: pip install workos",
                status_code=401,
            )

        client = WorkOS(api_key=self._workos_api_key, client_id=self._workos_client_id)

        # Generate auth URL
        auth_url = client.user_management.get_authorization_url(
            redirect_uri=_WORKOS_REDIRECT_URI,
            provider="authkit",
        )

        # Start localhost server for callback
        auth_response: dict[str, Any] = {}
        server_error: list[str] = []

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                auth_response.update({k: v[0] if len(v) == 1 else v for k, v in params.items()})

                has_error = "error" in params
                if has_error:
                    server_error.append(params.get("error_description", params.get("error", ["Unknown"]))[0])

                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                msg = "Authentication failed" if has_error else "WorkOS login successful. Continuing to Business Central..."
                self.wfile.write(f"<html><body><h2>{msg}</h2><p>You can close this tab.</p></body></html>".encode())

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", _WORKOS_PORT), CallbackHandler)
        server.timeout = _AUTH_TIMEOUT

        print("\nOpening browser for WorkOS login...", file=sys.stderr)
        print(f"If the browser doesn't open, visit:\n  {auth_url}\n", file=sys.stderr)
        webbrowser.open(auth_url)

        server_thread = threading.Thread(target=server.handle_request, daemon=True)
        server_thread.start()
        server_thread.join(timeout=_AUTH_TIMEOUT)
        server.server_close()

        if not auth_response:
            raise AuthError("WorkOS authentication timed out.", status_code=401)
        if server_error:
            raise AuthError(f"WorkOS authentication failed: {server_error[0]}", status_code=401)

        code = auth_response.get("code")
        if not code:
            raise AuthError("No authorization code received from WorkOS.", status_code=401)

        # Exchange code for user info
        result = client.user_management.authenticate_with_code(
            code=code,
        )

        user = result.user
        print(f"WorkOS: logged in as {user.email}", file=sys.stderr)

        # Get organization memberships to determine role
        memberships = client.user_management.list_organization_memberships(user_id=user.id)

        role_slug = "member"  # default
        for membership in memberships.data:
            if membership.status == "active":
                role_slug = membership.role.slug
                break

        # Map role to BC client_id
        bc_client_id = self._role_mapping.get(role_slug, self._default_bc_client_id)

        print(f"WorkOS: role={role_slug} → BC app {'admin' if bc_client_id != self._default_bc_client_id else 'standard'}", file=sys.stderr)

        # Cache WorkOS identity
        _save_workos_identity({
            "user_id": user.id,
            "email": user.email,
            "role": role_slug,
            "bc_client_id": bc_client_id,
        })

        return bc_client_id

    def clear_cache(self) -> None:
        """Clear BC token cache and WorkOS identity."""
        if self._bc_auth:
            self._bc_auth.clear_cache()
        _clear_workos_identity()


def _load_workos_identity() -> dict | None:
    """Load cached WorkOS identity from disk."""
    if _WORKOS_IDENTITY_FILE.is_file():
        try:
            return json.loads(_WORKOS_IDENTITY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_workos_identity(identity: dict) -> None:
    """Cache WorkOS identity to disk."""
    _WORKOS_IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WORKOS_IDENTITY_FILE.write_text(json.dumps(identity, indent=2))


def _clear_workos_identity() -> None:
    """Remove cached WorkOS identity."""
    if _WORKOS_IDENTITY_FILE.is_file():
        _WORKOS_IDENTITY_FILE.unlink()
