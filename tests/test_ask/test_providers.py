"""Entry-point context provider discovery (R8)."""

from __future__ import annotations

import sys
import textwrap
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from bcli.ask import collect_extra_context, discover_providers
from bcli.context import LastErrorRecord, ProfileSnapshot


def _install_provider_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, *, name: str
) -> None:
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)


def _patch_entrypoints(
    monkeypatch: pytest.MonkeyPatch, eps: list[EntryPoint]
) -> None:
    """Force ``entry_points(group=...)`` to return our test set."""

    def fake_entry_points(*, group: str | None = None):
        if group == "bcli.ask.context_providers":
            return eps
        return []

    import bcli.ask._providers as mod
    monkeypatch.setattr(mod, "entry_points", fake_entry_points)


def test_collect_extra_context_runs_only_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_provider_module(
        tmp_path,
        monkeypatch,
        """
        def provider(profile, last_error):
            return {"glossary.ESN": "Engine Serial Number"}
        """,
        name="_fake_provider_a",
    )
    _install_provider_module(
        tmp_path,
        monkeypatch,
        """
        def provider(profile, last_error):
            raise AssertionError("must not run")
        """,
        name="_fake_provider_b",
    )
    eps = [
        EntryPoint(
            name="alpha",
            value="_fake_provider_a:provider",
            group="bcli.ask.context_providers",
        ),
        EntryPoint(
            name="beta",
            value="_fake_provider_b:provider",
            group="bcli.ask.context_providers",
        ),
    ]
    _patch_entrypoints(monkeypatch, eps)

    out = collect_extra_context(
        profile=ProfileSnapshot(name="prod"),
        last_error=None,
        enabled=["alpha"],  # only alpha — beta must NOT run
    )
    assert out == {"glossary.ESN": "Engine Serial Number"}


def test_provider_failure_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    _install_provider_module(
        tmp_path,
        monkeypatch,
        """
        def provider(profile, last_error):
            raise RuntimeError("oops")
        """,
        name="_fake_provider_broken",
    )
    eps = [
        EntryPoint(
            name="broken",
            value="_fake_provider_broken:provider",
            group="bcli.ask.context_providers",
        ),
    ]
    _patch_entrypoints(monkeypatch, eps)
    out = collect_extra_context(
        profile=ProfileSnapshot(),
        last_error=None,
        enabled=["broken"],
    )
    assert out == {}


def test_unknown_provider_silently_skipped(monkeypatch) -> None:
    _patch_entrypoints(monkeypatch, [])
    out = collect_extra_context(
        profile=ProfileSnapshot(),
        last_error=None,
        enabled=["nonexistent"],
    )
    assert out == {}


def test_provider_receives_last_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_provider_module(
        tmp_path,
        monkeypatch,
        """
        def provider(profile, last_error):
            return {
                "saw_class": last_error.error_class if last_error else "",
                "profile_name": profile.name,
            }
        """,
        name="_fake_provider_inspect",
    )
    eps = [
        EntryPoint(
            name="inspect",
            value="_fake_provider_inspect:provider",
            group="bcli.ask.context_providers",
        ),
    ]
    _patch_entrypoints(monkeypatch, eps)
    out = collect_extra_context(
        profile=ProfileSnapshot(name="prod"),
        last_error=LastErrorRecord(
            timestamp="t",
            command="x",
            error_class="ValidationError",
            exit_code=2,
        ),
        enabled=["inspect"],
    )
    assert out["saw_class"] == "ValidationError"
    assert out["profile_name"] == "prod"
