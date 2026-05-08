"""Template resolution engine for workflow ${{ }} references."""

from __future__ import annotations

import re
from typing import Any

from bcli.errors import WorkflowError
from bcli.workflow._models import WorkflowContext

# Matches  ${{ steps.name.path }}  or  ${{ params.key }}
# Identifier set: word chars + dot (for nested step output paths) + hyphen
# (param names like ``vendor-no`` are commonly used by saved queries).
REFERENCE_PATTERN = re.compile(r"\$\{\{\s*(steps|params)\.([\w.\-]+)\s*\}\}")

# Same pattern, but must be the *entire* string (for type preservation).
FULL_REFERENCE_PATTERN = re.compile(
    r"^\s*\$\{\{\s*(steps|params)\.([\w.\-]+)\s*\}\}\s*$"
)


def resolve_references(value: Any, context: WorkflowContext) -> Any:
    """Recursively resolve ``${{ }}`` references in a YAML structure.

    - **dict / list**: recurse into children.
    - **str (full reference)**: return resolved value with original type preserved.
    - **str (embedded references)**: interpolate into the string (always returns str).
    - **Other types**: pass through unchanged.
    """
    if isinstance(value, dict):
        return {k: resolve_references(v, context) for k, v in value.items()}

    if isinstance(value, list):
        return [resolve_references(item, context) for item in value]

    if isinstance(value, str):
        # Full-string reference → type-preserving
        full_match = FULL_REFERENCE_PATTERN.match(value)
        if full_match:
            return _resolve_single(full_match, context)

        # Embedded references → string interpolation
        if REFERENCE_PATTERN.search(value):
            return REFERENCE_PATTERN.sub(
                lambda m: str(_resolve_single(m, context)), value
            )

    return value


# ─── Internal helpers ────────────────────────────────────────────────


def _resolve_single(match: re.Match, context: WorkflowContext) -> Any:
    """Resolve one ``${{ namespace.path }}`` match."""
    namespace = match.group(1)
    path = match.group(2)

    if namespace == "params":
        return _resolve_param(path, context)
    return _resolve_step(path, context)


def _resolve_param(path: str, context: WorkflowContext) -> Any:
    key = path.split(".")[0]
    if key not in context.params:
        raise WorkflowError(
            f"Parameter '{key}' not provided. Pass --set {key}=<value>"
        )
    return context.params[key]


def _resolve_step(path: str, context: WorkflowContext) -> Any:
    parts = path.split(".")
    step_name = parts[0]
    field_path = parts[1:]

    result = context.get_result(step_name)
    if result is None:
        available = list(context.step_results.keys())
        raise WorkflowError(
            f"Reference to undefined step '{step_name}'. "
            f"Available steps: {available}"
        )

    if result.status == "error":
        raise WorkflowError(
            f"Reference to failed step '{step_name}': {result.error}"
        )

    if not field_path:
        return result.data

    return _traverse(result.data, field_path, step_name)


def _traverse(data: Any, path_parts: list[str], step_name: str) -> Any:
    """Walk a dot-separated path into nested dicts / lists."""
    current = data
    for part in path_parts:
        if part == "length" and isinstance(current, list):
            return len(current)

        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                raise WorkflowError(
                    f"Step '{step_name}': expected numeric index for list, got '{part}'"
                )
            if index < 0 or index >= len(current):
                raise WorkflowError(
                    f"Step '{step_name}': index {index} out of range "
                    f"(list has {len(current)} items)"
                )
            current = current[index]

        elif isinstance(current, dict):
            if part not in current:
                available = list(current.keys())
                raise WorkflowError(
                    f"Step '{step_name}' output has no field '{part}'. "
                    f"Available fields: {available}"
                )
            current = current[part]

        else:
            raise WorkflowError(
                f"Step '{step_name}': cannot traverse into "
                f"{type(current).__name__} with key '{part}'"
            )

    return current
