"""In-process tool handlers — write safety is enforced HERE.

Every handler takes an :class:`~bcli.agent._runtime.AgentRuntime` as its
first argument plus the kwargs named by the tool's input schema, and
returns a JSON-able payload the model sees. Handlers never raise to the
model loop: errors come back as ``{"status": "error", "message": …}`` so
the model can self-correct (wrong endpoint name → use the suggestion)
instead of crashing the turn.

Write handlers run the runtime write gate *before* any HTTP call:
``disable_writes``, ``caution == "high"``, and production targets emit
``awaiting_approval`` events resolved by the REPL (or the headless
prompt). A decline returns a typed refusal. Approved writes execute
through :class:`bcli.client._safety.SafeContext` with an explicit
environment + company — never the profile-implied target.

The ``draft_batch`` handler is the plan-mode replacement for the whole
write tier: it renders a bcli batch YAML for human review and writes
nothing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from bcli.errors import BCLIError
from bcli.odata import Query

if TYPE_CHECKING:
    from bcli.agent._runtime import AgentRuntime


# ── helpers ───────────────────────────────────────────────────────────


def _error(message: str) -> dict[str, Any]:
    return {"status": "error", "message": message}


def _parse_json_object(data: str, *, field_name: str = "data") -> dict[str, Any]:
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{field_name} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must be a JSON object, got {type(parsed).__name__}")
    return parsed


def _meta_summary(meta: Any) -> dict[str, Any]:
    return {
        "entity_set_name": meta.entity_set_name,
        "description": meta.description,
        "supports": list(meta.supports),
        "domain": meta.domain,
        "caution": meta.caution,
        "key_field": meta.key_field,
        "is_custom": meta.is_custom,
    }


# ── read tier ─────────────────────────────────────────────────────────


async def handle_get(
    runtime: "AgentRuntime",
    *,
    endpoint: str,
    record_id: str | None = None,
    filter: str | None = None,  # noqa: A002 — matches the CLI flag name
    select: str | None = None,
    expand: str | None = None,
    orderby: str | None = None,
    top: int | None = None,
    skip: int | None = None,
    company: str | None = None,
) -> Any:
    query = Query()
    if filter:
        query = query.filter(filter)
    if select:
        query = query.select(*[f.strip() for f in select.split(",") if f.strip()])
    if expand:
        query = query.expand(*[f.strip() for f in expand.split(",") if f.strip()])
    if orderby:
        query = query.orderby(orderby)
    if top is not None:
        query = query.top(int(top))
    if skip is not None:
        query = query.skip(int(skip))

    client = runtime.client
    try:
        company_id, _ = runtime.profile.resolve_company(company)
        url = client._resolve_url_for_target(
            runtime.profile.environment, company_id, endpoint, record_id=record_id,
        )
        transport = client._ensure_transport()
        data = await transport.get(url, params=query.to_params())
    except BCLIError as exc:
        return _error(str(exc))
    except ValueError:
        return _error("company='all' is not supported in agent tool calls; "
                      "pass one company alias at a time.")
    if record_id is not None:
        return data
    value = data.get("value", data) if isinstance(data, dict) else data
    out: dict[str, Any] = {"value": value}
    if isinstance(value, list):
        out["returned"] = len(value)
    if isinstance(data, dict) and "@odata.count" in data:
        out["total_count"] = data["@odata.count"]
    return out


async def handle_endpoint_search(
    runtime: "AgentRuntime", *, pattern: str,
) -> Any:
    if runtime.registry is None:
        return _error("No endpoint registry available for this session.")
    matches = runtime.registry.search(pattern)[:15]
    if not matches:
        return {"matches": [], "hint": f"No endpoints matched '{pattern}'."}
    return {"matches": [_meta_summary(m) for m in matches]}


async def handle_endpoint_info(runtime: "AgentRuntime", *, name: str) -> Any:
    if runtime.registry is None:
        return _error("No endpoint registry available for this session.")
    try:
        meta = runtime.registry.resolve(name)
    except BCLIError as exc:
        return _error(str(exc))
    info = _meta_summary(meta)
    info["field_names"] = list(meta.field_names)
    info["route"] = (
        {"publisher": meta.api_publisher, "group": meta.api_group,
         "version": meta.api_version}
        if meta.is_custom else {"standard": "api/v2.0"}
    )
    return info


async def handle_endpoint_fields(runtime: "AgentRuntime", *, name: str) -> Any:
    if runtime.registry is None:
        return _error("No endpoint registry available for this session.")
    try:
        meta = runtime.registry.resolve(name)
    except BCLIError as exc:
        return _error(str(exc))
    if meta.field_names:
        return {"endpoint": meta.entity_set_name,
                "field_names": list(meta.field_names),
                "source": "registry"}
    # Fall back to sampling one record (same trick `bcli endpoint fields`
    # uses) — the live record's keys are the ground truth.
    sample = await handle_get(runtime, endpoint=name, top=1)
    if isinstance(sample, dict) and sample.get("status") == "error":
        return sample
    records = sample.get("value") if isinstance(sample, dict) else None
    if not records:
        return _error(
            f"Endpoint '{name}' has no records to sample and no captured "
            "field list — field names unknown."
        )
    fields = [k for k in records[0] if not k.startswith("@")]
    return {"endpoint": meta.entity_set_name, "field_names": fields,
            "source": "sampled_record"}


async def handle_company_list(runtime: "AgentRuntime") -> Any:
    try:
        companies = await runtime.client.list_companies()
    except BCLIError as exc:
        return _error(str(exc))
    aliases = {
        alias: {"id": c.id, "name": c.name}
        for alias, c in runtime.profile.companies.items()
    }
    return {"companies": companies, "aliases": aliases}


async def handle_env_list(runtime: "AgentRuntime") -> Any:
    try:
        return {"environments": await runtime.client.list_environments()}
    except BCLIError as exc:
        return _error(str(exc))


async def handle_describe(runtime: "AgentRuntime") -> Any:
    profile = runtime.profile
    out: dict[str, Any] = {
        "profile": runtime.profile_name,
        "environment": profile.environment,
        "is_production": runtime.is_production,
        "company_id": profile.company_id,
        "company_name": profile.company_name,
        "constraints": {
            "disable_writes": getattr(profile, "disable_writes", False),
            "disable_standard_api": getattr(profile, "disable_standard_api", False),
            "allowed_categories": list(profile.allowed_categories or []),
        },
        "plan_mode": runtime.plan_mode,
    }
    if runtime.registry is not None:
        endpoints = runtime.registry.list_all()
        out["endpoint_count"] = len(endpoints)
        out["endpoints"] = [
            {"name": m.entity_set_name, "caution": m.caution, "domain": m.domain}
            for m in endpoints[:200]
        ]
    return out


# ── write tier (gated) ────────────────────────────────────────────────


async def _gated_write(
    runtime: "AgentRuntime",
    *,
    method: str,
    endpoint: str,
    payload: Any,
    company: str | None,
    operation,
) -> Any:
    """Common gate → SafeContext → execute path for all write handlers."""
    decision = await runtime.gate_write(
        method=method, endpoint=endpoint, payload=payload,
    )
    if not decision.approved:
        return decision.refusal()
    try:
        sw = runtime.safe_context(company)
        return await operation(sw)
    except BCLIError as exc:
        return _error(str(exc))
    except ValueError:
        return _error("company='all' is not supported for writes; "
                      "pass one company alias at a time.")


async def handle_post(
    runtime: "AgentRuntime",
    *,
    endpoint: str,
    data: str,
    company: str | None = None,
) -> Any:
    try:
        body = _parse_json_object(data)
    except ValueError as exc:
        return _error(str(exc))
    domain = runtime.domain_for(endpoint)
    return await _gated_write(
        runtime, method="POST", endpoint=endpoint, payload=body, company=company,
        operation=lambda sw: sw.post(endpoint, body, domain=domain),
    )


async def handle_patch(
    runtime: "AgentRuntime",
    *,
    endpoint: str,
    record_id: str,
    data: str,
    etag: str | None = None,
    company: str | None = None,
) -> Any:
    try:
        body = _parse_json_object(data)
    except ValueError as exc:
        return _error(str(exc))
    domain = runtime.domain_for(endpoint)
    return await _gated_write(
        runtime, method="PATCH", endpoint=endpoint, payload=body, company=company,
        operation=lambda sw: sw.patch(
            endpoint, record_id, body, domain=domain, etag=etag or "*",
        ),
    )


async def handle_delete(
    runtime: "AgentRuntime",
    *,
    endpoint: str,
    record_id: str,
    etag: str | None = None,
    company: str | None = None,
) -> Any:
    domain = runtime.domain_for(endpoint)
    return await _gated_write(
        runtime, method="DELETE", endpoint=endpoint,
        payload={"record_id": record_id}, company=company,
        operation=lambda sw: sw.delete(
            endpoint, record_id, domain=domain, etag=etag or "*",
        ),
    )


async def handle_action(
    runtime: "AgentRuntime",
    *,
    endpoint: str,
    record_id: str,
    action_name: str,
    data: str | None = None,
    company: str | None = None,
) -> Any:
    body: dict[str, Any] = {}
    if data:
        try:
            body = _parse_json_object(data)
        except ValueError as exc:
            return _error(str(exc))
    ns_action = action_name if "." in action_name else f"Microsoft.NAV.{action_name}"
    composed = f"{endpoint}({record_id})/{ns_action}"
    domain = runtime.domain_for(endpoint)
    return await _gated_write(
        runtime, method=f"ACTION {ns_action}", endpoint=endpoint,
        payload={"record_id": record_id, "body": body}, company=company,
        operation=lambda sw: sw.post(composed, body, domain=domain),
    )


async def handle_attach_upload(
    runtime: "AgentRuntime",
    *,
    parent_type: str,
    parent_id: str,
    file_path: str,
    file_name: str | None = None,
    company: str | None = None,  # noqa: ARG001 — upload binds to profile company
) -> Any:
    decision = await runtime.gate_write(
        method="ATTACH UPLOAD", endpoint="documentAttachments",
        payload={"parent_type": parent_type, "parent_id": parent_id,
                 "file_path": file_path},
    )
    if not decision.approved:
        return decision.refusal()
    try:
        return await runtime.client.upload_attachment(
            parent_type, parent_id, file_path, file_name=file_name,
        )
    except (BCLIError, OSError) as exc:
        return _error(str(exc))


async def handle_batch_run(
    runtime: "AgentRuntime",
    *,
    file: str,
    dry_run: bool | None = None,
    set: str | None = None,  # noqa: A002 — matches the CLI flag name
) -> Any:
    """Run a batch YAML through the real ``bcli batch run`` CLI.

    The batch engine (ledger, step chaining, rollback) lives in the CLI
    layer, which the SDK must not import — so this handler shells out to
    the installed ``bcli`` binary, exactly like the MCP server does.
    The agent-side approval gate runs first; ``--yes`` is passed only
    *after* the human approved (or the run is a dry-run).
    """
    if not dry_run:
        decision = await runtime.gate_write(
            method="BATCH RUN", endpoint="batch",
            payload={"file": file, "set": set},
        )
        if not decision.approved:
            return decision.refusal()

    import asyncio as _asyncio
    import shutil
    import sys as _sys

    if shutil.which("bcli"):
        argv = ["bcli"]
    else:
        argv = [_sys.executable, "-m", "bcli_cli.app"]
    if runtime.profile_name:
        argv += ["--profile", runtime.profile_name]
    argv += ["batch", "run", file, "--format", "json"]
    if dry_run:
        argv.append("--dry-run")
    else:
        argv.append("--yes")
    if set:
        argv += ["--set", set]

    proc = await _asyncio.create_subprocess_exec(
        *argv,
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out_text = stdout.decode(errors="replace").strip()
    err_text = stderr.decode(errors="replace").strip()
    if proc.returncode != 0:
        return _error(
            f"bcli batch run exited {proc.returncode}: {err_text or out_text}"
        )
    return {"status": "ok", "dry_run": bool(dry_run),
            "output": out_text or err_text}


# ── plan mode ─────────────────────────────────────────────────────────


async def handle_draft_batch(
    runtime: "AgentRuntime", *, name: str, steps: str,
) -> Any:
    """Render proposed writes as a bcli batch YAML — writes nothing."""
    try:
        parsed = json.loads(steps)
    except (json.JSONDecodeError, TypeError) as exc:
        return _error(f"steps is not valid JSON: {exc}")
    if not isinstance(parsed, list) or not parsed:
        return _error("steps must be a non-empty JSON array of step objects.")

    rendered_steps: list[dict[str, Any]] = []
    for i, step in enumerate(parsed):
        if not isinstance(step, dict):
            return _error(f"steps[{i}] must be a JSON object.")
        action = step.get("action", "")
        if action not in ("post", "patch", "delete"):
            return _error(
                f"steps[{i}].action must be one of post/patch/delete, "
                f"got {action!r}."
            )
        if not step.get("endpoint"):
            return _error(f"steps[{i}].endpoint is required.")
        entry: dict[str, Any] = {
            "name": step.get("name") or f"step_{i + 1}",
            "action": action,
            "endpoint": step["endpoint"],
        }
        if step.get("record_id"):
            entry["record_id"] = step["record_id"]
        if step.get("data") is not None:
            entry["data"] = step["data"]
        rendered_steps.append(entry)

    doc = {"name": name, "steps": rendered_steps}
    yaml_text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    return {
        "status": "drafted",
        "batch_yaml": yaml_text,
        "next_step": (
            "Present this YAML to the operator. Nothing has been "
            "written. The operator reviews and runs it with "
            "'bcli batch run <file> --dry-run' then without --dry-run."
        ),
    }


# ── dispatch ──────────────────────────────────────────────────────────

HANDLERS: dict[tuple[str, ...], Any] = {
    ("get",): handle_get,
    ("endpoint", "search"): handle_endpoint_search,
    ("endpoint", "info"): handle_endpoint_info,
    ("endpoint", "fields"): handle_endpoint_fields,
    ("company", "list"): handle_company_list,
    ("env", "list"): handle_env_list,
    ("describe",): handle_describe,
    ("post",): handle_post,
    ("patch",): handle_patch,
    ("delete",): handle_delete,
    ("action",): handle_action,
    ("attach", "upload"): handle_attach_upload,
    ("batch", "run"): handle_batch_run,
    ("agent", "draft-batch"): handle_draft_batch,
}


def get_handler(path: tuple[str, ...]):
    """Handler for a tool path; raises ``KeyError`` for unknown paths."""
    return HANDLERS[path]


__all__ = ["HANDLERS", "get_handler"]
