"""Configuration loading with layered merge."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomlkit

from bcli.config._defaults import CONFIG_DIR, CONFIG_FILE, PROJECT_CONFIG_FILE
from bcli.config._model import BCConfig


def _find_project_config() -> Path | None:
    """Walk up from CWD looking for .bcli.toml."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / PROJECT_CONFIG_FILE
        if candidate.is_file():
            return candidate
    return None


def _load_toml(path: Path) -> dict:
    """Load a TOML file, return empty dict if missing."""
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (immutable — returns new dict)."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _apply_env_overrides(data: dict) -> dict:
    """Apply BCLI_* environment variable overrides."""
    env_map = {
        "BCLI_PROFILE": ("defaults", "profile"),
        "BCLI_FORMAT": ("defaults", "format"),
        "BCLI_TIMEOUT": ("defaults", "timeout"),
    }
    result = dict(data)
    for env_var, path in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            section, key = path
            if section not in result:
                result[section] = {}
            result[section] = dict(result[section])
            result[section][key] = value
    return result


def _sanitise_project_config(data: dict, source: Path) -> dict:
    """Strip sections from project ``.bcli.toml`` that could load arbitrary code.

    The CLI auto-discovers ``.bcli.toml`` by walking up from the working
    directory, so a malicious repo can drop one in. The telemetry layer
    accepts a ``backend = "module:Class"`` string and resolves it via
    ``import_module`` (`src/bcli/telemetry/_factory.py`), which means a
    project-level override could execute arbitrary Python on `bcli`
    invocation. Block that surface entirely: project config may turn
    telemetry **off** (``[telemetry] enabled = false``) but cannot point at
    a custom backend or change the connection string. Anything that
    actually loads code stays in global config (``~/.config/bcli/config.toml``).
    """
    telemetry = data.get("telemetry")
    if not isinstance(telemetry, dict):
        return data

    safe_keys = {"enabled"}
    rejected = sorted(k for k in telemetry.keys() if k not in safe_keys)
    if not rejected and "enabled" not in telemetry:
        # Nothing to strip and nothing useful left — drop the whole section.
        result = dict(data)
        result.pop("telemetry", None)
        return result

    if rejected:
        # Drop the unsafe keys and warn — never silently accept them.
        import warnings
        warnings.warn(
            f"Ignoring [telemetry] keys {rejected} from project config "
            f"{source}: only 'enabled' is honoured at the project layer "
            f"(custom backends and connection strings must live in the "
            f"global config to prevent arbitrary code execution from a "
            f"checked-out repo).",
            stacklevel=3,
        )

    cleaned_telemetry = {k: telemetry[k] for k in telemetry if k in safe_keys}
    result = dict(data)
    if cleaned_telemetry:
        result["telemetry"] = cleaned_telemetry
    else:
        result.pop("telemetry", None)
    return result


def load_config() -> BCConfig:
    """Load configuration with layered merge.

    Resolution order:
    1. Global config (~/.config/bcli/config.toml)
    2. Project config (.bcli.toml in CWD or parent) — sanitised first to
       prevent a checked-out malicious repo from injecting a custom
       telemetry backend (which would execute arbitrary Python on import).
    3. Environment variables (BCLI_*)
    """
    global_data = _load_toml(CONFIG_FILE)
    project_config = _find_project_config()
    project_data = (
        _sanitise_project_config(_load_toml(project_config), project_config)
        if project_config
        else {}
    )

    merged = _deep_merge(global_data, project_data)
    merged = _apply_env_overrides(merged)

    if not merged:
        return BCConfig()

    return BCConfig.model_validate(merged)


def save_config(config: BCConfig) -> Path:
    """Save configuration to the global config file using tomlkit for proper TOML output."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    doc = tomlkit.document()

    # Defaults section
    defaults = tomlkit.table()
    defaults.add("profile", config.defaults.profile)
    defaults.add("format", config.defaults.format)
    defaults.add("page_size", config.defaults.page_size)
    defaults.add("timeout", config.defaults.timeout)
    doc.add("defaults", defaults)

    # Profile sections
    if config.profiles:
        profiles = tomlkit.table(is_super_table=True)
        for name, profile in config.profiles.items():
            ptable = tomlkit.table()
            ptable.add("tenant_id", profile.tenant_id)
            ptable.add("environment", profile.environment)
            if profile.company_id:
                ptable.add("company_id", profile.company_id)
            if profile.company_name:
                ptable.add("company_name", profile.company_name)
            ptable.add("auth_method", profile.auth_method)
            if profile.client_id:
                ptable.add("client_id", profile.client_id)
            if profile.client_secret_env:
                ptable.add("client_secret_env", profile.client_secret_env)
            if profile.api_publisher:
                ptable.add("api_publisher", profile.api_publisher)
            if profile.api_group:
                ptable.add("api_group", profile.api_group)
            if profile.api_version:
                ptable.add("api_version", profile.api_version)
            if profile.disable_standard_api:
                ptable.add("disable_standard_api", True)

            # Company aliases
            if profile.companies:
                companies = tomlkit.table(is_super_table=True)
                for alias, company in profile.companies.items():
                    ctable = tomlkit.table()
                    ctable.add("id", company.id)
                    if company.name:
                        ctable.add("name", company.name)
                    companies.add(alias, ctable)
                ptable.add("companies", companies)

            profiles.add(name, ptable)
        doc.add("profiles", profiles)

    CONFIG_FILE.write_text(tomlkit.dumps(doc))
    return CONFIG_FILE


def update_config_section(section: str, values: dict) -> Path:
    """Surgically update one section of the global config file.

    Unlike :func:`save_config` (which rebuilds the whole document from a
    :class:`BCConfig` and would drop sections it doesn't model into TOML,
    e.g. ``[agent]`` / ``[ask]``), this loads the existing file with
    tomlkit — preserving comments and unrelated sections — and only
    sets the given keys. Used by the agent setup wizard and the
    subscription-consent flow.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        doc = tomlkit.parse(CONFIG_FILE.read_text())
    else:
        doc = tomlkit.document()
    if section not in doc:
        doc[section] = tomlkit.table()
    for key, value in values.items():
        doc[section][key] = value
    CONFIG_FILE.write_text(tomlkit.dumps(doc))
    return CONFIG_FILE
