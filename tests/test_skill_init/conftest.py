"""Shared fixtures for the ``bcli skill init`` wizard tests.

The wizard reads ``bcli describe --format json``, asks 3-4 questions via
Rich prompts, and writes:

* zero or more new saved-query entries into
  ``~/.config/bcli/queries/<profile>.yaml`` (each gated by ``[y/N]``);
* a fresh ``~/.claude/skills/bcli-<user>/SKILL.md``;
* a state cache at ``~/.config/bcli/skills/.last-init.json``.

Every test isolates ``HOME`` to ``tmp_path`` so the suite can run in
parallel without polluting the developer's real config dirs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


_FAKE_DESCRIBE: dict[str, Any] = {
    "version": "0.1",
    "tool": "bcli",
    "tool_version": "0.4.0",
    "profile": "finance_sandbox",
    "commands": [
        {
            "path": ["get"], "summary": "GET records from a Business Central entity.",
            "options": [], "positionals": [], "effects": ["read"],
            "supported_formats": ["json"],
        },
        {
            "path": ["q"], "summary": "Run a saved query (no OData required)",
            "options": [], "positionals": [], "effects": ["read"],
            "supported_formats": ["json"],
        },
    ],
    "registry": {
        "tier_1_custom_count": 3,
        "tier_2_standard_enabled": True,
        "endpoints": [
            {"entity": "customers", "tier": "standard", "domain": "standard",
             "ops": ["GET", "POST"], "fields_known": 12, "fields_discovered_at": None},
            {"entity": "vendors", "tier": "custom", "domain": "finance",
             "ops": ["GET", "POST"], "fields_known": 8, "fields_discovered_at": None},
            {"entity": "salesInvoices", "tier": "standard", "domain": "standard",
             "ops": ["GET"], "fields_known": 0, "fields_discovered_at": None},
        ],
    },
    "profile_constraints": {
        "disable_writes": False,
        "disable_standard_api": False,
        "allowed_categories": None,
    },
}


_FAKE_SAVED_QUERIES: dict[str, Any] = {
    "queries": {
        "vendor-by-no": {
            "description": "Look up a vendor by number",
            "endpoint": "vendors",
            "params": {"no": {"required": True}},
            "filter": "number eq '${{ params.no }}'",
        },
        "open-invoices": {
            "description": "Outstanding sales invoices",
            "endpoint": "salesInvoices",
            "filter": "status eq 'Open'",
        },
        "customer-by-name": {
            "description": "Customer lookup by display name",
            "endpoint": "customers",
            "params": {"name": {"required": True}},
            "filter": "displayName eq '${{ params.name }}'",
        },
    },
}


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Scope HOME (and Path.home) to tmp_path so the wizard's writes
    land in test-only directories."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def fake_describe(monkeypatch: pytest.MonkeyPatch):
    """Stub the ``bcli describe`` subprocess so the wizard sees a known
    payload instead of trying to fork a real CLI."""
    def _payload(profile: str | None = None) -> dict[str, Any]:
        # Return a copy so tests that mutate don't leak across cases.
        return json.loads(json.dumps(_FAKE_DESCRIBE))

    monkeypatch.setattr(
        "bcli_cli.commands.skill_init_cmd._load_describe_payload",
        _payload,
    )
    return _FAKE_DESCRIBE


@pytest.fixture
def seeded_saved_queries(isolated_home: Path) -> Path:
    """Drop a saved-queries YAML under the isolated config dir so the
    wizard has something to project."""
    import yaml
    queries_dir = isolated_home / ".config" / "bcli" / "queries"
    queries_dir.mkdir(parents=True, exist_ok=True)
    path = queries_dir / "finance_sandbox.yaml"
    path.write_text(yaml.safe_dump(_FAKE_SAVED_QUERIES), encoding="utf-8")
    return path


@pytest.fixture
def interview_answers(monkeypatch: pytest.MonkeyPatch):
    """Inject scripted answers to the Rich prompts. Tests pass a list of
    ``(prompt_substring, answer)`` pairs; the patched ``Prompt.ask`` /
    ``Confirm.ask`` walks them in order."""
    scripted: list[tuple[str, str]] = []

    def _ask(prompt: str, *, default: Any = None, **kwargs):
        for needle, answer in scripted:
            if needle in prompt:
                return answer
        return default

    def _confirm(prompt: str, *, default: bool = False, **kwargs):
        for needle, answer in scripted:
            if needle in prompt:
                return _coerce_bool(answer)
        return default

    monkeypatch.setattr("rich.prompt.Prompt.ask", _ask)
    monkeypatch.setattr("rich.prompt.Confirm.ask", _confirm)
    return scripted


def _coerce_bool(answer: Any) -> bool:
    if isinstance(answer, bool):
        return answer
    return str(answer).strip().lower() in {"y", "yes", "true", "1"}
