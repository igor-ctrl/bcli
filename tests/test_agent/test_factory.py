"""Backend dispatch + Null fallback (mirror of test_ask/test_factory.py)."""

from __future__ import annotations

import logging
import sys
import textwrap
from pathlib import Path

import pytest

from bcli.agent import NullAgentBackend, get_agent_backend
from bcli.config._model import AgentConfig


def _install_test_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, *, name: str
) -> None:
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)


def test_none_config_returns_null() -> None:
    assert isinstance(get_agent_backend(None), NullAgentBackend)


def test_default_backend_is_null() -> None:
    cfg = AgentConfig()
    backend = get_agent_backend(cfg)
    assert isinstance(backend, NullAgentBackend)
    assert backend.is_active is False


def test_unknown_backend_falls_back_to_null(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="bcli.agent"):
        result = get_agent_backend(AgentConfig(backend="nonexistent_backend"))
    assert isinstance(result, NullAgentBackend)


def test_custom_backend_loaded_by_import_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_test_module(
        tmp_path,
        monkeypatch,
        """
        class FakeBackend:
            is_active = True

            def __init__(self, model):
                self.model = model

            @classmethod
            def from_config(cls, config):
                return cls(model=config.model or "fake")

            async def start_session(self, *, system_prompt, tools, runtime):
                ...

            async def send(self, user_msg):
                from bcli.agent import AgentEvent
                yield AgentEvent(kind="turn_complete", text="ok")

            async def close(self):
                ...
        """,
        name="_bcli_fake_agent_mod",
    )
    backend = get_agent_backend(
        AgentConfig(backend="_bcli_fake_agent_mod:FakeBackend", model="custom-1")
    )
    assert backend.is_active is True
    assert getattr(backend, "model", None) == "custom-1"


def test_malformed_spec_falls_back_to_null(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="bcli.agent"):
        backend = get_agent_backend(AgentConfig(backend="no_colon_here"))
    assert isinstance(backend, NullAgentBackend)


def test_from_config_raise_falls_back_to_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    _install_test_module(
        tmp_path,
        monkeypatch,
        """
        class BoomBackend:
            is_active = True

            @classmethod
            def from_config(cls, config):
                raise RuntimeError("boom")
        """,
        name="_bcli_boom_agent_mod",
    )
    with caplog.at_level(logging.WARNING, logger="bcli.agent"):
        backend = get_agent_backend(AgentConfig(backend="_bcli_boom_agent_mod:BoomBackend"))
    assert isinstance(backend, NullAgentBackend)


def test_missing_from_config_falls_back_to_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    _install_test_module(
        tmp_path,
        monkeypatch,
        """
        class IncompleteBackend:
            is_active = True
        """,
        name="_bcli_incomplete_agent_mod",
    )
    with caplog.at_level(logging.WARNING, logger="bcli.agent"):
        backend = get_agent_backend(
            AgentConfig(backend="_bcli_incomplete_agent_mod:IncompleteBackend")
        )
    assert isinstance(backend, NullAgentBackend)


async def test_null_backend_emits_setup_hint() -> None:
    backend = NullAgentBackend()
    events = [ev async for ev in backend.send("hi")]
    kinds = [e.kind for e in events]
    assert "error" in kinds
    assert any("backend" in e.error for e in events if e.kind == "error")
    assert kinds[-1] == "turn_complete"
