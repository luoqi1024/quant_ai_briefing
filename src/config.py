"""Application configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is declared for runtime.
    load_dotenv = None


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    database_path: str = "quant_data.db"
    log_level: str = "INFO"
    xiaomi_ai_api_key: str | None = None
    xiaomi_ai_url: str | None = None
    xiaomi_ai_model: str | None = None
    wecom_corpid: str | None = None
    wecom_agentid: str | None = None
    wecom_secret: str | None = None


class ConfigError(ValueError):
    """Raised when required runtime configuration is missing."""


def load_settings() -> Settings:
    """Load settings from process environment.

    Full validation is added with the modules that need each external service.
    """

    if load_dotenv is not None:
        load_dotenv(dotenv_path=_project_env_path())

    return Settings(
        database_path=os.getenv("DATABASE_PATH", "quant_data.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
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
        if not settings.xiaomi_ai_api_key:
            missing.append("XIAOMI_AI_API_KEY")
        if not settings.xiaomi_ai_url:
            missing.append("XIAOMI_AI_URL")
        if not settings.xiaomi_ai_model:
            missing.append("XIAOMI_AI_MODEL")

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
