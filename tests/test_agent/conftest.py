"""Fixtures wrapping the shared fakes in :mod:`_helpers`.

The test dir has no ``__init__.py``; conftest runs before pytest adds the
dir to ``sys.path``, so add it here first, then import the sibling.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402

from _helpers import FakeMeta, FakeProfile, FakeRegistry  # noqa: E402


@pytest.fixture
def fake_profile() -> FakeProfile:
    return FakeProfile()


@pytest.fixture
def fake_registry() -> FakeRegistry:
    return FakeRegistry({
        "vendors": FakeMeta("vendors", caution="low"),
        "journalLines": FakeMeta("journalLines", caution="high", domain="finance"),
    })
