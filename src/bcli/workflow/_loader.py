"""Safe YAML loading for workflow files.

YAML 1.1 (PyYAML's default) parses bare ``no:``, ``yes:``, ``on:``,
``off:`` as booleans. BC fields use ``no`` as a primary-key field
(account number, line number) — silently coercing those to
``False`` produces broken workflows that hit BC with payloads like
``{"false": "6260-..."}`` instead of ``{"no": "6260-..."}``.

Rather than coerce-and-guess (which spelling did the user intend —
``no``? ``No``? ``NO``?), this loader rejects boolean keys with a
clear, actionable error. Authors quote the key (``\"no\":``) and
re-run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from bcli.errors import WorkflowError


def load_workflow_yaml(source: str | Path) -> Any:
    """Load a workflow YAML and reject YAML-1.1 boolean-key traps.

    Accepts a Path or a raw YAML string. Returns the parsed object.
    Raises ``WorkflowError`` if any dict key was parsed as a bool —
    the error message names the offending path so the author can
    quote the key in their YAML.
    """
    text = (
        source.read_text(encoding="utf-8")
        if isinstance(source, Path)
        else source
    )
    obj = yaml.safe_load(text)
    _reject_bool_keys(obj, path="<root>", source=source)
    return obj


def _reject_bool_keys(obj: Any, *, path: str, source: str | Path) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_path = f"{path}.{key!r}"
            if isinstance(key, bool):
                literal = "yes" if key else "no"
                where = f" in {source}" if isinstance(source, Path) else ""
                raise WorkflowError(
                    f"YAML key parsed as boolean {key} at {path}"
                    f"{where}. The bare word {literal!r} is YAML 1.1 "
                    f"boolean syntax. Quote it as \"{literal}\": to use "
                    f"as a string field name."
                )
            _reject_bool_keys(value, path=child_path, source=source)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _reject_bool_keys(item, path=f"{path}[{i}]", source=source)
