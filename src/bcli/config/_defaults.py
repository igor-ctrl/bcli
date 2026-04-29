"""Default values for bcli configuration."""

from __future__ import annotations

from pathlib import Path

# Base URL for Business Central online
BC_BASE_URL = "https://api.businesscentral.dynamics.com/v2.0"

# Standard API path (Microsoft v2.0)
BC_STANDARD_API_PATH = "api/v2.0"

# OAuth2 scope for Business Central
BC_SCOPE = "https://api.businesscentral.dynamics.com/.default"

# Entra ID authority base
ENTRA_AUTHORITY_BASE = "https://login.microsoftonline.com"

# Config directory
CONFIG_DIR = Path.home() / ".config" / "bcli"

# Config file
CONFIG_FILE = CONFIG_DIR / "config.toml"

# Token cache
TOKEN_CACHE_FILE = CONFIG_DIR / "tokens.json"

# Stable per-laptop installation id used as a low-cardinality dimension
# on telemetry events. Generated once on first emission, then reused. The
# file is plaintext; it carries no PII (it's a random UUID), so the goal
# is durability, not secrecy. See ``bcli.telemetry.events._install_id``.
INSTALL_ID_FILE = CONFIG_DIR / "install-id"

# Custom registries directory
REGISTRIES_DIR = CONFIG_DIR / "registries"

# Project-level config filename
PROJECT_CONFIG_FILE = ".bcli.toml"

# Defaults
DEFAULT_FORMAT = "table"
DEFAULT_PAGE_SIZE = 100
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 3

# Rate limiting (BC's limit is 600 req/min per environment)
BC_RATE_LIMIT_PER_MINUTE = 600
