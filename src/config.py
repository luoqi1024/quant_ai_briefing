"""Application configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import dotenv_values, load_dotenv
except ImportError:  # pragma: no cover - dependency is declared for runtime.
    dotenv_values = None
    load_dotenv = None


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    database_path: str = "quant_data.db"
    log_level: str = "INFO"
    ai_api_key: str | None = None
    ai_url: str | None = None
    ai_model: str | None = None
    xiaomi_ai_api_key: str | None = None
    xiaomi_ai_url: str | None = None
    xiaomi_ai_model: str | None = None
    wecom_corpid: str | None = None
    wecom_agentid: str | None = None
    wecom_secret: str | None = None

    @property
    def resolved_ai_api_key(self) -> str | None:
        """Return the generic AI key, falling back to legacy Xiaomi config."""

        return self.ai_api_key or self.xiaomi_ai_api_key

    @property
    def resolved_ai_url(self) -> str | None:
        """Return the generic AI URL, falling back to legacy Xiaomi config."""

        return self.ai_url or self.xiaomi_ai_url

    @property
    def resolved_ai_model(self) -> str | None:
        """Return the generic AI model, falling back to legacy Xiaomi config."""

        return self.ai_model or self.xiaomi_ai_model


class ConfigError(ValueError):
    """Raised when required runtime configuration is missing."""


def load_settings() -> Settings:
    """Load settings from process environment.

    Full validation is added with the modules that need each external service.
    """

    env_path = _project_env_path()
    if load_dotenv is not None:
        load_dotenv(dotenv_path=env_path)
    file_settings = dotenv_values(env_path) if dotenv_values is not None else {}

    return Settings(
        database_path=os.getenv("DATABASE_PATH", "quant_data.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        ai_api_key=_config_value(file_settings, "AI_API_KEY")
        or _config_value(file_settings, "XIAOMI_AI_API_KEY"),
        ai_url=_config_value(file_settings, "AI_URL")
        or _config_value(file_settings, "XIAOMI_AI_URL"),
        ai_model=_config_value(file_settings, "AI_MODEL")
        or _config_value(file_settings, "XIAOMI_AI_MODEL"),
        xiaomi_ai_api_key=os.getenv("XIAOMI_AI_API_KEY"),
        xiaomi_ai_url=os.getenv("XIAOMI_AI_URL"),
        xiaomi_ai_model=os.getenv("XIAOMI_AI_MODEL"),
        wecom_corpid=os.getenv("WECOM_CORPID"),
        wecom_agentid=os.getenv("WECOM_AGENTID"),
        wecom_secret=os.getenv("WECOM_SECRET"),
    )


def validate_settings(
    settings: Settings,
    *,
    dry_run: bool = False,
    send: bool = False,
) -> None:
    """Validate required settings for the selected runtime mode."""

    missing: list[str] = []
    if not settings.database_path:
        missing.append("DATABASE_PATH")

    if not dry_run:
        if not settings.resolved_ai_api_key:
            missing.append("AI_API_KEY")
        if not settings.resolved_ai_url:
            missing.append("AI_URL")
        if not settings.resolved_ai_model:
            missing.append("AI_MODEL")

    if send and not dry_run:
        if not settings.wecom_corpid:
            missing.append("WECOM_CORPID")
        if not settings.wecom_agentid:
            missing.append("WECOM_AGENTID")
        if not settings.wecom_secret:
            missing.append("WECOM_SECRET")

    if missing:
        raise ConfigError(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Set these environment variables or use --dry-run for local testing."
        )


def _project_env_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".env"


def _config_value(file_settings: dict[str, str | None], name: str) -> str | None:
    file_value = file_settings.get(name)
    return file_value or os.getenv(name)
