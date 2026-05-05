"""Single source of truth: the installed package's metadata.

Reading from importlib.metadata avoids the drift bug where pyproject.toml
got bumped but a hardcoded __version__ string in this module didn't —
the symptom was `bcli --version` reporting an older version than the
wheel actually shipped.

Falls back to a placeholder for editable installs that haven't been
registered with metadata yet (rare, but cleaner than crashing).
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bc-cli")
except PackageNotFoundError:  # pragma: no cover — only triggers in dev sandboxes
    __version__ = "0.0.0+unknown"
