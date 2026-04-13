"""Tests for configuration loading."""

from bcli.config._model import BCConfig, BCDefaults, BCProfile


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


def test_get_profile_not_found():
    import pytest
    from bcli.errors import ConfigError

    config = BCConfig()
    with pytest.raises(ConfigError, match="not found"):
        config.get_profile("nonexistent")


def test_profile_model():
    p = BCProfile(
        tenant_id="abc",
        environment="Production",
        client_id="xyz",
        client_secret_env="MY_SECRET",
        api_publisher="beautech",
        api_group="technical",
        api_version="v1.5",
    )
    assert p.auth_method == "client_credentials"
    assert p.api_publisher == "beautech"
