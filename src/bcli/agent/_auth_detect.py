"""Credential detection for the harness-owned backends.

Pure environment / filesystem checks — no optional SDK imports — so the
setup wizard and the consent gate can classify the auth path before
anything heavy loads.

Classification:

* ``"api_key"``      — a sanctioned programmatic key is present; no
                       consent needed.
* ``"subscription"`` — only subscription credentials are detectable
                       (Claude Code login / ``~/.codex/auth.json``);
                       the explicit consent gate applies.
* ``"none"``         — nothing usable found.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

AuthKind = str  # "api_key" | "subscription" | "none"


def detect_claude_auth(*, home: Path | None = None) -> AuthKind:
    """Classify how a claude-code backend session would authenticate."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api_key"
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "subscription"
    base = home or Path.home()
    # Claude Code stores login credentials under ~/.claude (keychain on
    # macOS, credentials file elsewhere); the binary on PATH with a
    # config dir is the practical signal that a subscription login exists.
    if (base / ".claude" / ".credentials.json").is_file():
        return "subscription"
    if shutil.which("claude") and (base / ".claude").is_dir():
        return "subscription"
    return "none"


def detect_codex_auth(*, home: Path | None = None) -> AuthKind:
    """Classify how a codex backend session would authenticate."""
    if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return "api_key"
    base = home or Path.home()
    if (base / ".codex" / "auth.json").is_file():
        return "subscription"
    return "none"


def claude_code_available() -> bool:
    return shutil.which("claude") is not None


def codex_available() -> bool:
    return shutil.which("codex") is not None


__all__ = [
    "claude_code_available",
    "codex_available",
    "detect_claude_auth",
    "detect_codex_auth",
]
