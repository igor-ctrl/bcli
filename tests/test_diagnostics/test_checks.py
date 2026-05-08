"""Unit tests for diagnostic check primitives."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bcli.config import BCConfig, BCProfile, BCDefaults
from bcli.diagnostics._checks import (
    CheckContext,
    CheckStatus,
    check_active_profile,
    check_auth_mode,
    check_bundle,
    check_company,
    check_environment,
    check_field_coverage,
    check_registry,
    check_saved_queries,
    check_tenant,
    check_token_cache,
    run_all_checks,
)


def _ctx(
    tmp_path: Path,
    *,
    profile: BCProfile | None = None,
    profile_name: str = "default",
    skip_network: bool = True,
) -> CheckContext:
    config = BCConfig(
        defaults=BCDefaults(profile=profile_name),
        profiles={profile_name: profile} if profile else {},
    )
    return CheckContext(
        config=config,
        profile=profile,
        profile_name=profile_name,
        bundle_dir=tmp_path / "bundles",
        token_cache_path=tmp_path / "tokens.json",
        queries_dir=tmp_path / "queries",
        registries_dir=tmp_path / "registries",
        skip_network=skip_network,
    )


def _profile(**overrides) -> BCProfile:
    base = dict(
        tenant_id="tenant-abc",
        environment="production",
        company_id="00000000-0000-0000-0000-000000000001",
        company_name="Acme",
        auth_method="device_code",
        client_id="client-xyz",
    )
    base.update(overrides)
    return BCProfile(**base)


# ─── individual checks ────────────────────────────────────────────────


def test_active_profile_ok(tmp_path):
    ctx = _ctx(tmp_path, profile=_profile())
    assert check_active_profile(ctx).status is CheckStatus.OK


def test_active_profile_fail_when_missing(tmp_path):
    ctx = _ctx(tmp_path, profile_name="ghost")
    result = check_active_profile(ctx)
    assert result.status is CheckStatus.FAIL
    assert "ghost" in result.summary


def test_tenant_fail_when_blank(tmp_path):
    p = _profile()
    object.__setattr__(p, "tenant_id", "")  # bypass pydantic validation for the test
    ctx = _ctx(tmp_path, profile=p)
    assert check_tenant(ctx).status is CheckStatus.FAIL


def test_environment_ok(tmp_path):
    ctx = _ctx(tmp_path, profile=_profile())
    assert check_environment(ctx).status is CheckStatus.OK


def test_company_warn_when_unset(tmp_path):
    ctx = _ctx(tmp_path, profile=_profile(company_id=None, company_name=None))
    assert check_company(ctx).status is CheckStatus.WARN


def test_auth_mode_unknown_warns(tmp_path):
    ctx = _ctx(tmp_path, profile=_profile(auth_method="hotp"))
    assert check_auth_mode(ctx).status is CheckStatus.WARN


def test_token_cache_missing_is_info(tmp_path):
    ctx = _ctx(tmp_path, profile=_profile())
    assert check_token_cache(ctx).status is CheckStatus.INFO


def test_token_cache_corrupt_warns(tmp_path):
    cache = tmp_path / "tokens.json"
    cache.write_text("{ this is not json", encoding="utf-8")
    ctx = _ctx(tmp_path, profile=_profile())
    assert check_token_cache(ctx).status is CheckStatus.WARN


def test_token_cache_valid_entry(tmp_path):
    cache = tmp_path / "tokens.json"
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    cache.write_text(
        json.dumps(
            {"tenant-abc:client-xyz": {"access_token": "x", "expires_at": expires}}
        ),
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path, profile=_profile())
    result = check_token_cache(ctx)
    assert result.status is CheckStatus.OK
    assert "valid for active profile" in result.summary


def test_token_cache_all_expired(tmp_path):
    cache = tmp_path / "tokens.json"
    expires = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cache.write_text(
        json.dumps(
            {"tenant-abc:client-xyz": {"access_token": "x", "expires_at": expires}}
        ),
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path, profile=_profile())
    result = check_token_cache(ctx)
    assert result.status is CheckStatus.INFO
    assert "expired" in result.summary


def test_token_cache_other_tenant_does_not_lie(tmp_path):
    """A valid cached token for a *different* tenant must not be reported OK
    for the active profile. This is the regression that prompted the
    profile-scoped scoping."""
    cache = tmp_path / "tokens.json"
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    cache.write_text(
        json.dumps(
            {
                "other-tenant:other-client": {
                    "access_token": "x",
                    "expires_at": expires,
                }
            }
        ),
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path, profile=_profile())
    result = check_token_cache(ctx)
    assert result.status is CheckStatus.INFO
    assert "active profile" in result.summary


def test_registry_scoped_with_no_imports_fails(tmp_path):
    ctx = _ctx(tmp_path, profile=_profile(disable_standard_api=True))
    result = check_registry(ctx)
    assert result.status is CheckStatus.FAIL
    assert "scoped profile" in result.summary


def test_registry_standard_loads(tmp_path):
    ctx = _ctx(tmp_path, profile=_profile(disable_standard_api=False))
    result = check_registry(ctx)
    assert result.status is CheckStatus.OK
    assert "standard" in result.summary


def test_field_coverage_high(tmp_path):
    registries = tmp_path / "registries"
    registries.mkdir()
    (registries / "default.json").write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "entity_set_name": f"e{i}",
                        "category": "custom",
                        "publisher": "x",
                        "group": "y",
                        "version": "v1.0",
                        "field_names": ["a", "b"],
                    }
                    for i in range(8)
                ]
                + [
                    {
                        "entity_set_name": "missing",
                        "category": "custom",
                        "publisher": "x",
                        "group": "y",
                        "version": "v1.0",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    ctx = _ctx(
        tmp_path,
        profile=_profile(disable_standard_api=True),
    )
    result = check_field_coverage(ctx)
    assert result.status is CheckStatus.OK
    assert "8/9" in result.summary


def test_field_coverage_low_warns(tmp_path):
    registries = tmp_path / "registries"
    registries.mkdir()
    (registries / "default.json").write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "entity_set_name": f"e{i}",
                        "category": "custom",
                        "publisher": "x",
                        "group": "y",
                        "version": "v1.0",
                    }
                    for i in range(10)
                ]
            }
        ),
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path, profile=_profile(disable_standard_api=True))
    assert check_field_coverage(ctx).status is CheckStatus.WARN


def test_saved_queries_missing_for_scoped_profile_warns(tmp_path):
    ctx = _ctx(tmp_path, profile=_profile(disable_standard_api=True))
    assert check_saved_queries(ctx).status is CheckStatus.WARN


def test_saved_queries_count(tmp_path):
    qdir = tmp_path / "queries"
    qdir.mkdir()
    (qdir / "default.yaml").write_text(
        "queries:\n  a:\n    endpoint: x\n  b:\n    endpoint: y\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path, profile=_profile())
    result = check_saved_queries(ctx)
    assert result.status is CheckStatus.OK
    assert "2" in result.summary


def test_bundle_missing_is_info(tmp_path):
    ctx = _ctx(tmp_path, profile=_profile())
    assert check_bundle(ctx).status is CheckStatus.INFO


def test_bundle_old_warns(tmp_path):
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    (bundles / "default.manifest.json").write_text(
        json.dumps({"version": "1.0.0", "published_at": old_ts}),
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path, profile=_profile())
    result = check_bundle(ctx)
    assert result.status is CheckStatus.WARN
    assert "refresh" in result.hint


# ─── orchestration ────────────────────────────────────────────────────


def test_run_all_checks_swallows_exceptions(tmp_path):
    """A buggy check function must not crash the whole report."""
    ctx = _ctx(tmp_path, profile=_profile())

    def boom(_ctx):
        raise RuntimeError("intentional")

    results = run_all_checks(ctx, checks=(boom,))
    assert len(results) == 1
    assert results[0].status is CheckStatus.FAIL
    assert "RuntimeError" in results[0].summary


def test_run_all_checks_runs_default_set(tmp_path):
    ctx = _ctx(tmp_path, profile=_profile())
    results = run_all_checks(ctx)
    # Sanity: at least the documented checks run, in roughly the order we declared.
    names = [r.name for r in results]
    assert "active profile" in names
    assert "registry" in names
    assert "bc connectivity" in names
    # skip_network=True means connectivity is INFO, not FAIL.
    conn = next(r for r in results if r.name == "bc connectivity")
    assert conn.status is CheckStatus.INFO
