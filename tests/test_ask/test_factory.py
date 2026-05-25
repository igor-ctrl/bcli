"""Backend dispatch + Null fallback (mirror of test_extract/test_factory.py)."""

from __future__ import annotations

import logging
import sys
import textwrap
from pathlib import Path

import pytest

from bcli.ask import get_asker
from bcli.ask._protocol import NullAsker
from bcli.config._model import AskConfig


def _install_test_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, *, name: str
) -> None:
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)


def test_none_config_returns_null() -> None:
    assert isinstance(get_asker(None), NullAsker)


def test_default_backend_is_null() -> None:
    cfg = AskConfig()
    assert isinstance(get_asker(cfg), NullAsker)
    assert get_asker(cfg).is_active is False


def test_unknown_backend_falls_back_to_null(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="bcli.ask"):
        result = get_asker(AskConfig(backend="nonexistent_backend"))
    assert isinstance(result, NullAsker)


def test_custom_backend_loaded_by_import_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_test_module(
        tmp_path,
        monkeypatch,
        """
        from bcli.ask._protocol import AskAnswer


        class FakeAsker:
            is_active = True

            def __init__(self, model):
                self.model = model

            @classmethod
            def from_config(cls, config):
                return cls(model=config.model or "fake")

            def ask(self, *, question, bundle):
                return AskAnswer(answer="fake reply", model=self.model)
        """,
        name="_bcli_fake_asker_mod",
    )
    backend = get_asker(
        AskConfig(
            backend="_bcli_fake_asker_mod:FakeAsker",
            model="custom-model-1",
        )
    )
    assert backend.is_active is True
    assert getattr(backend, "model", None) == "custom-model-1"


def test_malformed_spec_falls_back_to_null(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="bcli.ask"):
        backend = get_asker(AskConfig(backend="no_colon_here"))
    assert isinstance(backend, NullAsker)


def test_from_config_raise_falls_back_to_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    _install_test_module(
        tmp_path,
        monkeypatch,
        """
        class BoomAsker:
            is_active = True

            @classmethod
            def from_config(cls, config):
                raise RuntimeError("boom")

            def ask(self, *, question, bundle):
                ...
        """,
        name="_bcli_boom_asker_mod",
    )
    with caplog.at_level(logging.WARNING, logger="bcli.ask"):
        backend = get_asker(
            AskConfig(backend="_bcli_boom_asker_mod:BoomAsker")
        )
    assert isinstance(backend, NullAsker)


def test_missing_from_config_falls_back_to_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    _install_test_module(
        tmp_path,
        monkeypatch,
        """
        class IncompleteAsker:
            is_active = True

            def ask(self, *, question, bundle):
                ...
        """,
        name="_bcli_incomplete_asker_mod",
    )
    with caplog.at_level(logging.WARNING, logger="bcli.ask"):
        backend = get_asker(
            AskConfig(
                backend="_bcli_incomplete_asker_mod:IncompleteAsker"
            )
        )
    assert isinstance(backend, NullAsker)


def test_null_asker_returns_warning() -> None:
    asker = NullAsker()
    from bcli.context import ContextBundle

    answer = asker.ask(question="q", bundle=ContextBundle())
    assert answer.answer == ""
    assert any("backend" in w for w in answer.warnings)
