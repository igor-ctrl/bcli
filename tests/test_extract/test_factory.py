"""Tests for the pluggable extract backend dispatch."""

from __future__ import annotations

import logging
import sys
import textwrap
from pathlib import Path

import pytest

from bcli.config._model import ExtractConfig
from bcli.extract import get_extractor
from bcli.extract._protocol import NullExtractor


def _install_test_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, *, name: str
) -> None:
    """Write a real .py module under tmp_path and put tmp_path on sys.path."""
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)


def test_none_config_returns_null() -> None:
    assert isinstance(get_extractor(None), NullExtractor)


def test_default_backend_is_null() -> None:
    cfg = ExtractConfig()
    assert isinstance(get_extractor(cfg), NullExtractor)
    assert get_extractor(cfg).is_active is False


def test_unknown_backend_falls_back_to_null(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="bcli.extract"):
        result = get_extractor(ExtractConfig(backend="nonexistent_backend"))
    assert isinstance(result, NullExtractor)


def test_custom_backend_loaded_by_import_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_test_module(
        tmp_path,
        monkeypatch,
        """
        from bcli.extract._protocol import ExtractionResult


        class FakeExtractor:
            is_active = True

            def __init__(self, model):
                self.model = model

            @classmethod
            def from_config(cls, config):
                return cls(model=config.model)

            def extract(self, pdf_path, schema):
                return ExtractionResult(schema_name=schema.name, model=self.model)
        """,
        name="_bcli_fake_extractor_mod",
    )

    backend = get_extractor(
        ExtractConfig(
            backend="_bcli_fake_extractor_mod:FakeExtractor",
            model="claude-sonnet-4-6",
        )
    )
    assert backend.is_active is True
    assert getattr(backend, "model", None) == "claude-sonnet-4-6"


def test_malformed_spec_falls_back_to_null(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="bcli.extract"):
        backend = get_extractor(ExtractConfig(backend="no_colon_here"))
    assert isinstance(backend, NullExtractor)


def test_from_config_raise_falls_back_to_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    _install_test_module(
        tmp_path,
        monkeypatch,
        """
        class BoomExtractor:
            is_active = True

            @classmethod
            def from_config(cls, config):
                raise RuntimeError("boom")

            def extract(self, pdf, schema):
                ...
        """,
        name="_bcli_boom_extractor_mod",
    )

    with caplog.at_level(logging.WARNING, logger="bcli.extract"):
        backend = get_extractor(
            ExtractConfig(backend="_bcli_boom_extractor_mod:BoomExtractor")
        )
    assert isinstance(backend, NullExtractor)


def test_missing_from_config_falls_back_to_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    _install_test_module(
        tmp_path,
        monkeypatch,
        """
        class IncompleteExtractor:
            is_active = True
            # no from_config

            def extract(self, pdf, schema):
                ...
        """,
        name="_bcli_incomplete_extractor_mod",
    )
    with caplog.at_level(logging.WARNING, logger="bcli.extract"):
        backend = get_extractor(
            ExtractConfig(backend="_bcli_incomplete_extractor_mod:IncompleteExtractor")
        )
    assert isinstance(backend, NullExtractor)
