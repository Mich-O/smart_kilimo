"""
config.py
Settings singleton for Smart Kilimo. Reads all configuration from environment
variables (via a .env file in development, real env vars in production).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class MissingSettingError(RuntimeError):
    """Raised when a required environment variable is not set."""


def _require(key: str) -> str:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        raise MissingSettingError(f"Required environment variable '{key}' is not set")
    return value


def _get(key: str, default: str) -> str:
    return os.getenv(key, default)


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    return int(raw) if raw is not None and raw.strip() != "" else default


def _get_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    return float(raw) if raw is not None and raw.strip() != "" else default


def _get_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    flask_env: str
    secret_key: str
    internal_api_token: str

    database_url: str

    at_username: str
    at_api_key: str
    at_sender_id: str
    at_environment: str

    weather_api_base_url: str
    weather_timezone: str
    weather_past_days: int
    rainfall_reset_threshold_mm: float

    daily_cycle_hour: int
    daily_cycle_minute: int
    reminder_interval_hours: int
    max_reminders: int

    default_admin_username: str
    default_admin_password: str

    seed_demo_data: bool

    @property
    def is_production(self) -> bool:
        return self.flask_env.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        flask_env=_get("FLASK_ENV", "development"),
        secret_key=_require("FLASK_SECRET_KEY"),
        internal_api_token=_require("INTERNAL_API_TOKEN"),
        database_url=_get("DATABASE_URL", "sqlite:///smart_kilimo.db"),
        at_username=_require("AT_USERNAME"),
        at_api_key=_require("AT_API_KEY"),
        at_sender_id=_get("AT_SENDER_ID", "SmartKilimo"),
        at_environment=_get("AT_ENVIRONMENT", "sandbox"),
        weather_api_base_url=_get(
            "WEATHER_API_BASE_URL", "https://api.open-meteo.com/v1/forecast"
        ),
        weather_timezone=_get("WEATHER_TIMEZONE", "Africa/Nairobi"),
        weather_past_days=_get_int("WEATHER_PAST_DAYS", 1),
        rainfall_reset_threshold_mm=_get_float("RAINFALL_RESET_THRESHOLD_MM", 5.0),
        daily_cycle_hour=_get_int("DAILY_CYCLE_HOUR", 6),
        daily_cycle_minute=_get_int("DAILY_CYCLE_MINUTE", 0),
        reminder_interval_hours=_get_int("REMINDER_INTERVAL_HOURS", 4),
        max_reminders=_get_int("MAX_REMINDERS", 3),
        default_admin_username=_get("DEFAULT_ADMIN_USERNAME", "admin"),
        default_admin_password=_get("DEFAULT_ADMIN_PASSWORD", "ChangeMe123!"),
        seed_demo_data=_get_bool("SEED_DEMO_DATA", False),
    )


# Module-level singleton for convenient `from config import settings`
settings = get_settings()
