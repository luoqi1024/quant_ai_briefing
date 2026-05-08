import pytest

from src.config import ConfigError, Settings, validate_settings


def test_validate_settings_allows_dry_run_without_external_config():
    validate_settings(Settings(database_path="test.db"), dry_run=True, send=False)


def test_validate_settings_requires_ai_config_outside_dry_run():
    with pytest.raises(ConfigError, match="XIAOMI_AI_API_KEY"):
        validate_settings(Settings(database_path="test.db"), dry_run=False, send=False)


def test_validate_settings_requires_wecom_config_when_sending():
    settings = Settings(
        database_path="test.db",
        xiaomi_ai_api_key="key",
        xiaomi_ai_url="https://example.invalid/chat",
        xiaomi_ai_model="model",
    )

    with pytest.raises(ConfigError, match="WECOM_CORPID"):
        validate_settings(settings, dry_run=False, send=True)
