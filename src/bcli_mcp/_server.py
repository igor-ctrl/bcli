"""FastMCP server with a dynamically-generated bcli tool surface.

Phase 5 of AIP v0.1. The server's tool list is no longer hand-written.
On startup, ``_build_server`` subprocesses ``bcli describe --format json``
and turns each command into one FastMCP tool via
:func:`bcli_mcp._tool_generator.generate_tools`. New CLI commands light
up automatically; deprecated ones disappear.

Two invocation paths:

* **Read** (``effects: ["read"]``) — append ``--format json``, parse
  stdout, return the parsed JSON.
* **Mutating** (``emits_result_envelope: True``) — pass
  ``--result-out <tmp>``, read the envelope back, return its content
  as the tool result. A ``status="failed"`` envelope surfaces as an
  MCP ``ToolError`` with the envelope's correlation id quoted for the
  agent to cite.

If ``bcli describe`` fails on startup (bcli not on PATH, broken install,
etc.), the server still starts — with zero tools registered and a stderr
warning. The operator can fix the install and reconnect.

The subprocess boundary inherits auth, retry, telemetry, profile gates
for free — that's the whole point. No Python imports from ``bcli`` core.
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import tempfile
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from bcli.result_envelope import read_envelope
from bcli_mcp._runner import (
    _strip_rich,
    run_bcli_json,
    run_bcli_with_envelope,
)
from bcli_mcp._tool_generator import GeneratedTool, generate_tools


# Map JSON Schema primitive types back to Python types so FastMCP's
# pydantic-based signature introspection picks up the right types.
_PYTHON_TYPE_FOR_JSON: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _python_type(json_type: str) -> type:
    return _PYTHON_TYPE_FOR_JSON.get(json_type, str)


def _apply_dynamic_signature(handler, tool: GeneratedTool) -> None:
    """Override ``handler`` so its signature matches the tool's input
    schema. FastMCP's tool registration walks the function signature
    (via pydantic) to build the JSON Schema agents see — without this
    override every tool would show up as ``inputs: object`` and lose
    its named parameters."""
    properties = tool.input_schema.get("properties", {})
    required = set(tool.input_schema.get("required", []))

    params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    for name, prop in properties.items():
        py_type = _python_type(prop.get("type", "string"))
        default = prop.get("default") if name not in required else inspect.Parameter.empty
        param = inspect.Parameter(
            name=name,
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if name in required else default,
            annotation=py_type,
        )
        params.append(param)
        annotations[name] = py_type
    # Every tool also accepts an optional ``profile`` override so the
    # agent can scope a single call without restarting the server.
    if "profile" not in annotations:
        params.append(inspect.Parameter(
            name="profile",
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=str | None,
        ))
        annotations["profile"] = str | None
    annotations["return"] = Any
    handler.__signature__ = inspect.Signature(params)
    handler.__annotations__ = annotations


def _load_describe_payload() -> dict[str, Any]:
    """Subprocess ``bcli describe --format json`` and return parsed JSON.

    Raises on any failure — caller decides whether to fall back to a
    zero-tools server.
    """
    proc = subprocess.run(
        ["bcli", "describe", "--format", "json"],
        capture_output=True, text=True, timeout=30.0, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"bcli describe exited {proc.returncode}: "
            f"{_strip_rich(proc.stderr or proc.stdout or '(no output)')}"
        )
    return json.loads(proc.stdout)


def _make_read_handler(tool: GeneratedTool):
    """Return an async callable FastMCP will register as the tool fn.

    The signature is rewritten by :func:`_apply_dynamic_signature` so
    FastMCP's pydantic-based introspection sees one keyword arg per
    input-schema property — preserves the tool's input schema as the
    agent actually receives it.
    """
    async def handler(**inputs: Any) -> Any:
        # Drop None values so optional args that the agent didn't fill
        # in don't pollute the argv builder.
        inputs = {k: v for k, v in inputs.items() if v is not None}
        profile = inputs.pop("profile", None)
        args = tool.build_argv(inputs)
        result = run_bcli_json(*args, profile=profile)
        # Single-record dict from ``bcli get <entity> <id>`` is wrapped
        # so the return type stays stable for the agent.
        if tool.name == "bcli_get" and isinstance(result, dict):
            return [result]
        return result
    handler.__name__ = tool.name
    handler.__doc__ = tool.description
    _apply_dynamic_signature(handler, tool)
    return handler


def _make_mutating_handler(tool: GeneratedTool):
    """Mutating handler: passes ``--result-out``, reads envelope back."""
    async def handler(**inputs: Any) -> dict[str, Any]:
        inputs = {k: v for k, v in inputs.items() if v is not None}
        profile = inputs.pop("profile", None)
        args = list(tool.build_argv(inputs))
        if profile:
            args = ["--profile", profile, *args]

        env = os.environ.copy()
        if profile:
            env["BCLI_PROFILE"] = profile

        # The envelope is written atomically by bcli; we just need a
        # private path the subprocess can write to and we can read.
        fd, env_path = tempfile.mkstemp(prefix="bcli-mcp-", suffix=".json")
        os.close(fd)
        try:
            exit_code, _stdout, stderr = run_bcli_with_envelope(
                args, env=env, capture_envelope_path=env_path,
            )
            try:
                envelope = read_envelope(env_path)
            except (OSError, ValueError, KeyError) as exc:
                # Envelope missing or malformed → bcli crashed before
                # writing. Surface the stderr as the error so the agent
                # can read it.
                raise ToolError(
                    f"bcli {' '.join(args)} exited {exit_code} without "
                    f"writing an envelope: {_strip_rich(stderr or '(no output)')}"
                ) from exc

            if envelope.status == "failed":
                corr = envelope.bc_correlation_id or "n/a"
                raise ToolError(
                    f"bcli {' '.join(args)} failed (exit_code="
                    f"{envelope.exit_code}, correlation_id={corr})"
                )
            # Return the envelope as a plain dict so MCP can serialize.
            from dataclasses import asdict
            return asdict(envelope)
        finally:
            try:
                os.unlink(env_path)
            except OSError:
                pass

    handler.__name__ = tool.name
    handler.__doc__ = tool.description
    _apply_dynamic_signature(handler, tool)
    return handler


def _register_tool(mcp: FastMCP, tool: GeneratedTool) -> None:
    """Register one generated tool with FastMCP.

    Read handlers get a thin wrapper; mutating handlers get the
    envelope-reading flow. Profile is always available as an extra
    kwarg the agent can pass to scope the call.
    """
    if tool.emits_envelope:
        fn = _make_mutating_handler(tool)
    else:
        fn = _make_read_handler(tool)
    mcp.tool(name=tool.name, description=tool.description)(fn)


def _build_server(*, describe_payload: dict[str, Any] | None = None) -> FastMCP:
    """Build a FastMCP server with tools generated from describe.

    ``describe_payload`` is injected for tests; production calls
    :func:`_load_describe_payload` to subprocess the real CLI.
    """
    mcp = FastMCP("bcli")
    if describe_payload is None:
        try:
            describe_payload = _load_describe_payload()
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"bcli-mcp: warning — could not load describe payload "
                f"({exc}). Starting with zero tools; fix the install and "
                "reconnect.\n"
            )
            return mcp
    for tool in generate_tools(describe_payload):
        _register_tool(mcp, tool)
    return mcp


# Module-level singleton used by ``python -m bcli_mcp``. Built lazily so
# the import is cheap and tests can patch ``_build_server`` cleanly.
mcp: FastMCP | None = None


def get_server() -> FastMCP:
    """Return the module-level server, building it on first call."""
    global mcp
    if mcp is None:
        mcp = _build_server()
    return mcp
