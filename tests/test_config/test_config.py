"""Tests for configuration loading, merging, and company resolution."""


import pytest

from bcli.config._loader import _apply_env_overrides, _deep_merge, load_config, save_config
from bcli.config._model import BCConfig, BCDefaults, BCProfile, CompanyAlias
from bcli.errors import ConfigError


# ── Model basics ──────────────────────────────────────────────────────────

def test_default_config():
    config = BCConfig()
    assert config.defaults.profile == "default"
    assert config.defaults.format == "table"
    assert config.defaults.page_size == 100


def test_get_profile():
    config = BCConfig(
        profiles={
            "prod": BCProfile(tenant_id="t1", environment="Production"),
            "sandbox": BCProfile(tenant_id="t2", environment="Sandbox"),
        }
    )
    p = config.get_profile("prod")
    assert p.environment == "Production"


def test_get_profile_default():
    config = BCConfig(
        defaults=BCDefaults(profile="sandbox"),
        profiles={
            "sandbox": BCProfile(tenant_id="t2", environment="Sandbox"),
        },
    )
    p = config.get_profile()
    assert p.environment == "Sandbox"


def test_get_profile_no_profiles_configured():
    config = BCConfig()
    with pytest.raises(ConfigError, match="bcli config init"):
        config.get_profile("nonexistent")


def test_get_profile_not_found():
    config = BCConfig(
        profiles={"prod": BCProfile(tenant_id="t1", environment="Production")}
    )
    with pytest.raises(ConfigError, match="not found"):
        config.get_profile("nonexistent")


def test_profile_model():
    p = BCProfile(
        tenant_id="abc",
        environment="Production",
        client_id="xyz",
        client_secret_env="MY_SECRET",
        api_publisher="contoso",
        api_group="technical",
        api_version="v1.5",
    )
    assert p.auth_method == "client_credentials"
    assert p.api_publisher == "contoso"


# ── _deep_merge ───────────────────────────────────────────────────────────

def test_deep_merge_flat():
    base = {"a": 1, "b": 2}
    override = {"b": 3, "c": 4}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": 3, "c": 4}
    # Immutability: originals unchanged
    assert base == {"a": 1, "b": 2}


def test_deep_merge_nested():
    base = {"defaults": {"profile": "default", "format": "table"}, "extra": True}
    override = {"defaults": {"profile": "prod"}}
    result = _deep_merge(base, override)
    assert result == {"defaults": {"profile": "prod", "format": "table"}, "extra": True}


def test_deep_merge_empty_override():
    base = {"a": 1}
    assert _deep_merge(base, {}) == {"a": 1}


def test_deep_merge_empty_base():
    override = {"a": 1}
    assert _deep_merge({}, override) == {"a": 1}


# ── _apply_env_overrides ──────────────────────────────────────────────────

def test_apply_env_overrides_profile(monkeypatch):
    monkeypatch.setenv("BCLI_PROFILE", "staging")
    data: dict = {}
    result = _apply_env_overrides(data)
    assert result["defaults"]["profile"] == "staging"


def test_apply_env_overrides_format(monkeypatch):
    monkeypatch.setenv("BCLI_FORMAT", "json")
    data = {"defaults": {"profile": "default"}}
    result = _apply_env_overrides(data)
    assert result["defaults"]["format"] == "json"
    assert result["defaults"]["profile"] == "default"


def test_apply_env_overrides_timeout(monkeypatch):
    monkeypatch.setenv("BCLI_TIMEOUT", "120")
    result = _apply_env_overrides({})
    assert result["defaults"]["timeout"] == "120"


def test_apply_env_overrides_no_env():
    """No BCLI_ env vars set — data passes through unchanged."""
    data = {"defaults": {"profile": "prod"}}
    result = _apply_env_overrides(data)
    assert result == data


# ── load_config ───────────────────────────────────────────────────────────

def test_load_config_no_files(monkeypatch, tmp_path):
    """With no config files and no env vars, returns defaults."""
    monkeypatch.setattr("bcli.config._loader.CONFIG_FILE", tmp_path / "nope.toml")
    monkeypatch.setattr("bcli.config._loader._find_project_config", lambda: None)
    monkeypatch.delenv("BCLI_PROFILE", raising=False)
    monkeypatch.delenv("BCLI_FORMAT", raising=False)
    monkeypatch.delenv("BCLI_TIMEOUT", raising=False)
    config = load_config()
    assert config.defaults.profile == "default"
    assert config.profiles == {}


def test_load_config_global_file(monkeypatch, tmp_path):
    """Loads profiles from global config file."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[defaults]\nprofile = "test"\n\n'
        '[profiles.test]\ntenant_id = "t1"\nenvironment = "Sandbox"\n'
    )
    monkeypatch.setattr("bcli.config._loader.CONFIG_FILE", config_file)
    monkeypatch.setattr("bcli.config._loader._find_project_config", lambda: None)
    monkeypatch.delenv("BCLI_PROFILE", raising=False)
    monkeypatch.delenv("BCLI_FORMAT", raising=False)
    monkeypatch.delenv("BCLI_TIMEOUT", raising=False)
    config = load_config()
    assert config.defaults.profile == "test"
    p = config.get_profile("test")
    assert p.tenant_id == "t1"


# ── save_config round-trip ────────────────────────────────────────────────

def test_save_config_round_trip(monkeypatch, tmp_path):
    """save_config then load produces equivalent config."""
    config_dir = tmp_path / "bcli"
    config_file = config_dir / "config.toml"
    monkeypatch.setattr("bcli.config._loader.CONFIG_DIR", config_dir)
    monkeypatch.setattr("bcli.config._loader.CONFIG_FILE", config_file)
    monkeypatch.setattr("bcli.config._loader._find_project_config", lambda: None)
    monkeypatch.delenv("BCLI_PROFILE", raising=False)
    monkeypatch.delenv("BCLI_FORMAT", raising=False)
    monkeypatch.delenv("BCLI_TIMEOUT", raising=False)

    original = BCConfig(
        defaults=BCDefaults(profile="prod", format="json", page_size=50, timeout=30),
        profiles={
            "prod": BCProfile(
                tenant_id="t-abc",
                environment="Production",
                company_id="c-123",
                company_name="Contoso Ltd",
                auth_method="client_credentials",
                client_id="app-id",
                client_secret_env="BCLI_SECRET",
                companies={
                    "LLC": CompanyAlias(id="c-123", name="Contoso Ltd"),
                    "Corp": CompanyAlias(id="c-456", name="Northwind Traders"),
                },
            ),
        },
    )

    save_config(original)
    loaded = load_config()

    assert loaded.defaults.profile == "prod"
    assert loaded.defaults.format == "json"
    assert loaded.defaults.page_size == 50
    assert loaded.defaults.timeout == 30
    p = loaded.get_profile("prod")
    assert p.tenant_id == "t-abc"
    assert p.environment == "Production"
    assert p.client_secret_env == "BCLI_SECRET"
    assert "LLC" in p.companies
    assert p.companies["LLC"].id == "c-123"
    assert "Corp" in p.companies


# ── resolve_company ───────────────────────────────────────────────────────

def _profile_with_companies() -> BCProfile:
    return BCProfile(
        tenant_id="t1",
        environment="Production",
        company_id="default-guid-000",
        company_name="Default Co",
        companies={
            "LLC": CompanyAlias(id="guid-llc", name="Contoso Ltd"),
            "Corp": CompanyAlias(id="guid-corp", name="Northwind Traders"),
        },
    )


def test_resolve_company_none_returns_default():
    p = _profile_with_companies()
    cid, name = p.resolve_company(None)
    assert cid == "default-guid-000"
    assert name == "Default Co"


def test_resolve_company_none_no_default():
    p = BCProfile(tenant_id="t1", environment="Sandbox")
    with pytest.raises(ConfigError, match="No company_id"):
        p.resolve_company(None)


def test_resolve_company_all_raises():
    p = _profile_with_companies()
    with pytest.raises(ValueError, match="all"):
        p.resolve_company("all")


def test_resolve_company_alias():
    p = _profile_with_companies()
    cid, name = p.resolve_company("LLC")
    assert cid == "guid-llc"
    assert name == "Contoso Ltd"


def test_resolve_company_alias_case_insensitive():
    p = _profile_with_companies()
    cid, name = p.resolve_company("llc")
    assert cid == "guid-llc"


def test_resolve_company_guid_passthrough():
    p = _profile_with_companies()
    guid = "REDACTED-1234-5678-abcd-000000000000"
    cid, name = p.resolve_company(guid)
    assert cid == guid
    assert name is None


def test_resolve_company_not_found():
    p = _profile_with_companies()
    with pytest.raises(ConfigError, match="not found"):
        p.resolve_company("Nope")
