"""Setup-wizard pure logic: backend detection + [agent] section assembly."""

from __future__ import annotations

from bcli.config._model import AgentConfig
from bcli_cli.repl._wizard import (
    build_agent_section,
    detect_backends,
    has_usable_backend,
)


def test_detect_backends_lists_all_choices() -> None:
    options = detect_backends()
    backends = {o.backend for o in options}
    assert "pydantic-ai" in backends
    assert "claude-code" in backends
    assert "codex" in backends
    # Three pydantic-ai entries (anthropic key / openai key / local).
    assert sum(o.backend == "pydantic-ai" for o in options) == 3


def test_build_agent_section_api_key_option() -> None:
    option = next(o for o in detect_backends() if o.key == "1")
    section = build_agent_section(option)
    assert section["backend"] == "pydantic-ai"
    assert section["model"] == "anthropic:claude-sonnet-4-5"
    assert "base_url" not in section


def test_build_agent_section_local_includes_base_url() -> None:
    option = next(o for o in detect_backends() if o.key == "3")
    section = build_agent_section(
        option, base_url="http://localhost:11434/v1", model="ollama:qwen2.5",
    )
    assert section["base_url"] == "http://localhost:11434/v1"
    assert section["model"] == "ollama:qwen2.5"


def test_has_usable_backend() -> None:
    assert has_usable_backend(None) is False
    assert has_usable_backend(AgentConfig()) is False  # default null
    assert has_usable_backend(AgentConfig(backend="pydantic-ai")) is True
