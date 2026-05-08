"""Diagnostic check primitives for ``bcli doctor``.

Each check is a small callable that takes a :class:`CheckContext` and returns
a :class:`CheckResult`. Checks never raise: a broken install must still
produce a clean report, otherwise the command is useless to the people who
need it most. Catch broadly, attribute the failure to the check, and move on.

The check list is intentionally flat (no orchestrator, no dependency graph).
A skipped check is still a result with status ``info`` so the report shows
what was inspected.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from bcli.config import BCConfig, BCProfile


class CheckStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"


@dataclass(frozen=True)
class CheckResult:
    """One row in the doctor report."""

    name: str
    status: CheckStatus
    summary: str
    hint: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_fail(self) -> bool:
        return self.status is CheckStatus.FAIL


@dataclass
class CheckContext:
    """Inputs every check is allowed to read.

    Constructed once at the top of ``bcli doctor`` so individual checks stay
    pure and trivially testable. ``config`` and ``profile`` are optional
    because doctor must run on a totally broken install — that path returns
    a single fail result and short-circuits.
    """

    config: BCConfig | None
    profile: BCProfile | None
    profile_name: str
    bundle_dir: Path
    token_cache_path: Path
    queries_dir: Path
    registries_dir: Path
    bcli_version: str = ""
    skip_network: bool = False


# ─── Individual checks ───────────────────────────────────────────────


def check_active_profile(ctx: CheckContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult(
            "active profile",
            CheckStatus.FAIL,
            "no config loaded",
            hint="run `bcli config init` to create one",
        )
    if ctx.profile is None:
        return CheckResult(
            "active profile",
            CheckStatus.FAIL,
            f"profile '{ctx.profile_name}' not found",
            hint=f"available: {', '.join(ctx.config.profiles) or '(none)'}",
        )
    return CheckResult(
        "active profile",
        CheckStatus.OK,
        ctx.profile_name,
    )


def check_tenant(ctx: CheckContext) -> CheckResult:
    if ctx.profile is None:
        return CheckResult("tenant", CheckStatus.INFO, "skipped (no profile)")
    if not ctx.profile.tenant_id:
        return CheckResult(
            "tenant",
            CheckStatus.FAIL,
            "tenant_id missing on profile",
            hint="add tenant_id to ~/.config/bcli/config.toml",
        )
    return CheckResult("tenant", CheckStatus.OK, ctx.profile.tenant_id)


def check_environment(ctx: CheckContext) -> CheckResult:
    if ctx.profile is None:
        return CheckResult("environment", CheckStatus.INFO, "skipped (no profile)")
    if not ctx.profile.environment:
        return CheckResult(
            "environment",
            CheckStatus.FAIL,
            "environment missing on profile",
            hint="set `environment = \"production\"` (or sandbox name) in config.toml",
        )
    return CheckResult("environment", CheckStatus.OK, ctx.profile.environment)


def check_company(ctx: CheckContext) -> CheckResult:
    if ctx.profile is None:
        return CheckResult("company", CheckStatus.INFO, "skipped (no profile)")
    if not ctx.profile.company_id:
        return CheckResult(
            "company",
            CheckStatus.WARN,
            "no default company configured",
            hint="run `bcli company list` then `bcli company use <alias-or-id>`",
        )
    name = ctx.profile.company_name or "(unnamed)"
    return CheckResult(
        "company",
        CheckStatus.OK,
        f"{name} ({ctx.profile.company_id[:8]}…)",
    )


def check_auth_mode(ctx: CheckContext) -> CheckResult:
    if ctx.profile is None:
        return CheckResult("auth", CheckStatus.INFO, "skipped (no profile)")
    mode = ctx.profile.auth_method or ""
    if mode in {"client_credentials", "device_code", "browser"}:
        return CheckResult("auth", CheckStatus.OK, mode)
    if not mode:
        return CheckResult(
            "auth",
            CheckStatus.FAIL,
            "auth_method missing",
            hint="set `auth_method = \"device_code\"` for finance/technical profiles",
        )
    return CheckResult(
        "auth",
        CheckStatus.WARN,
        f"unknown auth_method '{mode}'",
        hint="expected: client_credentials, device_code, or browser",
    )


def check_token_cache(ctx: CheckContext) -> CheckResult:
    """Inspect the on-disk token cache for the active profile.

    The cache is keyed by ``<tenant_id>:<client_id>``. A common support
    failure is "I have a valid token cached for some other tenant but not
    the active one" — reporting "ok" in that case is misleading. Scope to
    the active profile so the result reflects what the next CLI call will
    actually use.
    """
    if not ctx.token_cache_path.is_file():
        return CheckResult(
            "token cache",
            CheckStatus.INFO,
            "no cached token (you will be prompted on next call)",
        )
    try:
        data = json.loads(ctx.token_cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return CheckResult(
            "token cache",
            CheckStatus.WARN,
            f"unreadable: {type(e).__name__}",
            hint=f"delete {ctx.token_cache_path} and re-authenticate",
        )

    if not isinstance(data, dict) or not data:
        return CheckResult("token cache", CheckStatus.INFO, "empty")

    profile_key: str | None = None
    if ctx.profile and ctx.profile.tenant_id and ctx.profile.client_id:
        profile_key = f"{ctx.profile.tenant_id}:{ctx.profile.client_id}"

    now = datetime.now(timezone.utc)

    if profile_key is not None:
        entry = data.get(profile_key)
        if not isinstance(entry, dict):
            return CheckResult(
                "token cache",
                CheckStatus.INFO,
                "no cached token for active profile",
                hint="next call will trigger sign-in",
            )
        try:
            expires_at = datetime.fromisoformat(entry["expires_at"])
        except (KeyError, ValueError):
            return CheckResult(
                "token cache",
                CheckStatus.WARN,
                "cached entry has no parseable expiry",
                hint="run `bcli auth clear` then sign in again",
            )
        if expires_at <= now:
            return CheckResult(
                "token cache",
                CheckStatus.INFO,
                "active profile's token has expired",
                hint="next call will trigger sign-in",
            )
        minutes = int((expires_at - now).total_seconds() / 60)
        return CheckResult(
            "token cache",
            CheckStatus.OK,
            f"valid for active profile, expires in {minutes} min",
        )

    # Fall back to the cache-wide summary when we don't have enough info
    # to pick out the active profile's entry (e.g. profile load failed).
    valid = 0
    expired = 0
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        try:
            expires_at = datetime.fromisoformat(entry["expires_at"])
        except (KeyError, ValueError):
            continue
        if expires_at > now:
            valid += 1
        else:
            expired += 1
    if valid == 0 and expired > 0:
        return CheckResult(
            "token cache",
            CheckStatus.INFO,
            f"all {expired} cached tokens expired (profile-scope unavailable)",
            hint="next call will trigger re-authentication",
        )
    if valid == 0:
        return CheckResult("token cache", CheckStatus.INFO, "no valid entries")
    return CheckResult(
        "token cache",
        CheckStatus.INFO,
        f"{valid} cached entries (profile-scope unavailable)",
    )


def check_registry(ctx: CheckContext) -> CheckResult:
    if ctx.profile is None:
        return CheckResult("registry", CheckStatus.INFO, "skipped (no profile)")

    try:
        from bcli.registry._registry import EndpointRegistry

        reg = EndpointRegistry(
            profile_name=ctx.profile_name,
            disable_standard=ctx.profile.disable_standard_api,
            allowed_categories=ctx.profile.allowed_categories or None,
            allowed_endpoints=ctx.profile.allowed_endpoints or None,
        )
    except Exception as e:  # noqa: BLE001 — diagnostic, never re-raise
        return CheckResult(
            "registry",
            CheckStatus.FAIL,
            f"failed to load: {type(e).__name__}: {e}",
            hint="run `bcli endpoint list --debug` for the full traceback",
        )

    custom = reg.custom_count
    standard = reg.standard_count
    total = custom + standard
    if ctx.profile.disable_standard_api and custom == 0:
        return CheckResult(
            "registry",
            CheckStatus.FAIL,
            "scoped profile has zero custom endpoints",
            hint=(
                f"import a registry: `bcli registry import --from-json"
                f" <bundle.json> --profile {ctx.profile_name}`"
            ),
        )
    if total == 0:
        return CheckResult(
            "registry",
            CheckStatus.FAIL,
            "no endpoints available",
            hint="check `disable_standard_api` and custom registry path",
        )

    summary = f"{custom} custom"
    if not ctx.profile.disable_standard_api:
        summary += f" + {standard} standard"
    summary += " endpoints"
    return CheckResult("registry", CheckStatus.OK, summary)


def check_field_coverage(ctx: CheckContext) -> CheckResult:
    """For scoped profiles, count how many custom endpoints have field lists.

    Below 50% coverage is a warn — pre-flight `--filter` validation can't help
    the user without field names, and "did you mean" suggestions degrade.
    """
    if ctx.profile is None or not ctx.profile.disable_standard_api:
        return CheckResult("field coverage", CheckStatus.INFO, "n/a (standard profile)")

    registry_file = ctx.registries_dir / f"{ctx.profile_name}.json"
    if not registry_file.is_file():
        return CheckResult(
            "field coverage",
            CheckStatus.INFO,
            "no custom registry file yet",
        )
    try:
        raw = json.loads(registry_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return CheckResult(
            "field coverage",
            CheckStatus.WARN,
            f"registry unreadable: {type(e).__name__}",
        )

    endpoints = raw.get("endpoints", [])
    if not endpoints:
        return CheckResult("field coverage", CheckStatus.INFO, "no endpoints")
    with_fields = sum(1 for e in endpoints if e.get("field_names"))
    pct = int(100 * with_fields / len(endpoints))
    summary = f"{with_fields}/{len(endpoints)} endpoints ({pct}%)"
    if pct < 50:
        return CheckResult(
            "field coverage",
            CheckStatus.WARN,
            summary,
            hint="run `bcli endpoint fields <name>` on heavy-use endpoints to populate",
        )
    return CheckResult("field coverage", CheckStatus.OK, summary)


def check_saved_queries(ctx: CheckContext) -> CheckResult:
    queries_file = ctx.queries_dir / f"{ctx.profile_name}.yaml"
    if not queries_file.is_file():
        if ctx.profile is not None and ctx.profile.disable_standard_api:
            return CheckResult(
                "saved queries",
                CheckStatus.WARN,
                "no saved-query file for scoped profile",
                hint=f"create {queries_file} or pull from team bundle",
            )
        return CheckResult("saved queries", CheckStatus.INFO, "none defined")
    try:
        import yaml  # type: ignore[import-untyped]

        raw = yaml.safe_load(queries_file.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            "saved queries",
            CheckStatus.FAIL,
            f"unparseable: {type(e).__name__}: {e}",
            hint=f"fix YAML syntax in {queries_file}",
        )
    queries = raw.get("queries", {}) if isinstance(raw, dict) else {}
    return CheckResult("saved queries", CheckStatus.OK, f"{len(queries)} defined")


def check_bundle(ctx: CheckContext) -> CheckResult:
    """Report on the team bundle state. Bundles are phase-2 territory; until
    one is installed this check is informational only — never a fail."""
    manifest = ctx.bundle_dir / f"{ctx.profile_name}.manifest.json"
    if not manifest.is_file():
        return CheckResult(
            "team bundle",
            CheckStatus.INFO,
            "not installed (using local registry only)",
            hint="run `bcli config refresh` once your team has published a bundle",
        )
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return CheckResult(
            "team bundle",
            CheckStatus.WARN,
            f"manifest unreadable: {type(e).__name__}",
            hint="run `bcli config refresh --rollback` or re-import",
        )

    version = data.get("version", "?")
    published_at = data.get("published_at", "")
    age_hint = ""
    try:
        if published_at:
            ts = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - ts).days
            age_hint = f", {age_days}d old"
            if age_days > 30:
                return CheckResult(
                    "team bundle",
                    CheckStatus.WARN,
                    f"version {version}{age_hint}",
                    hint="run `bcli config refresh` to pull the latest",
                )
            if age_days > 7:
                return CheckResult(
                    "team bundle",
                    CheckStatus.WARN,
                    f"version {version}{age_hint}",
                    hint="consider running `bcli config refresh`",
                )
    except (ValueError, TypeError):
        age_hint = " (unparseable timestamp)"
    return CheckResult("team bundle", CheckStatus.OK, f"version {version}{age_hint}")


def check_bc_connectivity(ctx: CheckContext) -> CheckResult:
    """One unauthenticated probe to BC's public host.

    Uses ``httpx`` with ``trust_env=True`` so corporate HTTP/HTTPS proxies
    are honored — a previous version went straight to TCP, which falsely
    failed on machines that talk to BC via the company proxy. A 401 / 403
    / 404 from BC counts as "host is reachable, BC is fine" — those are
    its expected responses to a no-auth GET. Only network/proxy failures
    fail the check.
    """
    if ctx.skip_network:
        return CheckResult("bc connectivity", CheckStatus.INFO, "skipped (--skip-network)")
    host = "api.businesscentral.dynamics.com"
    url = f"https://{host}/v2.0/"
    try:
        import httpx

        with httpx.Client(timeout=5.0, trust_env=True, follow_redirects=False) as client:
            resp = client.get(url)
            return CheckResult(
                "bc connectivity",
                CheckStatus.OK,
                f"{host} reachable (HTTP {resp.status_code})",
            )
    except ImportError:
        # httpx is a hard dep of bcli, so this branch is paranoia. Falling
        # back to a TCP probe still beats refusing to render the report.
        try:
            with socket.create_connection((host, 443), timeout=5):
                return CheckResult(
                    "bc connectivity",
                    CheckStatus.OK,
                    f"{host}:443 TCP reachable (httpx unavailable)",
                )
        except OSError as e:
            return CheckResult(
                "bc connectivity",
                CheckStatus.FAIL,
                f"{host} unreachable ({type(e).__name__})",
                hint="check network / proxy / corporate firewall",
            )
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            "bc connectivity",
            CheckStatus.FAIL,
            f"{host} unreachable: {type(e).__name__}: {e}",
            hint="check network / corporate proxy (HTTPS_PROXY) / VPN",
        )


# ─── Orchestration ───────────────────────────────────────────────────


CheckFn = Callable[[CheckContext], CheckResult]

_DEFAULT_CHECKS: tuple[CheckFn, ...] = (
    check_active_profile,
    check_tenant,
    check_environment,
    check_company,
    check_auth_mode,
    check_token_cache,
    check_registry,
    check_field_coverage,
    check_saved_queries,
    check_bundle,
    check_bc_connectivity,
)


def run_all_checks(
    ctx: CheckContext,
    *,
    checks: tuple[CheckFn, ...] | None = None,
) -> list[CheckResult]:
    """Run every check, swallowing exceptions per check."""
    results: list[CheckResult] = []
    for check in (checks or _DEFAULT_CHECKS):
        try:
            results.append(check(ctx))
        except Exception as e:  # noqa: BLE001 — diagnostic, never re-raise
            results.append(
                CheckResult(
                    name=check.__name__.removeprefix("check_").replace("_", " "),
                    status=CheckStatus.FAIL,
                    summary=f"check raised {type(e).__name__}: {e}",
                    hint="this is a bug in `bcli doctor` — please report",
                )
            )
    return results
