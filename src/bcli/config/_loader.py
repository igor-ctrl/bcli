"""Configuration loading with layered merge."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from bcli.config._defaults import CONFIG_DIR, CONFIG_FILE, PROJECT_CONFIG_FILE
from bcli.config._model import BCConfig, BCDefaults, BCProfile
from bcli.errors import ConfigError


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


def load_config() -> BCConfig:
    """Load configuration with layered merge.

    Resolution order:
    1. Global config (~/.config/bcli/config.toml)
    2. Project config (.bcli.toml in CWD or parent)
    3. Environment variables (BCLI_*)
    """
    global_data = _load_toml(CONFIG_FILE)
    project_config = _find_project_config()
    project_data = _load_toml(project_config) if project_config else {}

    merged = _deep_merge(global_data, project_data)
    merged = _apply_env_overrides(merged)

    if not merged:
        return BCConfig()

    return BCConfig.model_validate(merged)


def save_config(config: BCConfig) -> Path:
    """Save configuration to the global config file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []

    # Defaults section
    lines.append("[defaults]")
    lines.append(f'profile = "{config.defaults.profile}"')
    lines.append(f'format = "{config.defaults.format}"')
    lines.append(f"page_size = {config.defaults.page_size}")
    lines.append(f"timeout = {config.defaults.timeout}")
    lines.append("")

    # Profile sections
    for name, profile in config.profiles.items():
        lines.append(f"[profiles.{name}]")
        lines.append(f'tenant_id = "{profile.tenant_id}"')
        lines.append(f'environment = "{profile.environment}"')
        if profile.company_id:
            lines.append(f'company_id = "{profile.company_id}"')
        if profile.company_name:
            lines.append(f'company_name = "{profile.company_name}"')
        lines.append(f'auth_method = "{profile.auth_method}"')
        if profile.client_id:
            lines.append(f'client_id = "{profile.client_id}"')
        if profile.client_secret_env:
            lines.append(f'client_secret_env = "{profile.client_secret_env}"')
        if profile.api_publisher:
            lines.append(f'api_publisher = "{profile.api_publisher}"')
        if profile.api_group:
            lines.append(f'api_group = "{profile.api_group}"')
        if profile.api_version:
            lines.append(f'api_version = "{profile.api_version}"')
        lines.append("")

        # Company aliases
        for alias, company in profile.companies.items():
            lines.append(f"[profiles.{name}.companies.{alias}]")
            lines.append(f'id = "{company.id}"')
            if company.name:
                lines.append(f'name = "{company.name}"')
            lines.append("")

    CONFIG_FILE.write_text("\n".join(lines))
    return CONFIG_FILE
