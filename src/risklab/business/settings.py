"""Environment-only application settings; errors never include secret values."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class SettingsError(ValueError):
    """Safe configuration failure, suitable for startup diagnostics."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: str = "development"
    database_url: SecretStr | None = None
    storage_root: Path = Path("data/application/objects")
    pool_size: int = Field(default=3, ge=1, le=20)
    pool_timeout: int = Field(default=5, ge=1, le=60)
    origin: str = "http://localhost:3000"
    # Operator-pinned demo/scoring inputs. Browsers never select these paths.
    catalog_file: Path | None = None
    catalog_sha256: str | None = None
    release_file: Path | None = None
    model_file: Path | None = None
    comparison_file: Path | None = None
    config_dir: Path = Path("configs")
    root: Path = Path(".")


def _secret(env: Mapping[str, str], name: str) -> str | None:
    direct = env.get(name)
    filename = env.get(name + "_FILE")
    if direct is not None and filename is not None:
        raise SettingsError(f"Set only {name} or {name}_FILE")
    if filename is not None:
        try:
            direct = Path(filename).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raise SettingsError(f"Cannot read {name}_FILE") from None
    if direct is not None and not direct:
        raise SettingsError(f"{name} must not be empty")
    return direct


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read only named application settings, never load .env or ML YAML implicitly."""
    env = os.environ if env is None else env
    url = _secret(env, "RISKLAB_DATABASE_URL")
    if url is not None:
        try:
            parsed = make_url(url)
            if parsed.drivername != "postgresql+psycopg" or not parsed.database:
                raise ValueError
        except (ValueError, TypeError, ArgumentError):
            raise SettingsError("RISKLAB_DATABASE_URL must be a PostgreSQL psycopg URL") from None
    environment = env.get("RISKLAB_ENVIRONMENT", "development")
    if environment not in {"development", "test", "production"}:
        raise SettingsError("Invalid RISKLAB_ENVIRONMENT")
    origin = env.get("RISKLAB_ORIGIN", "http://localhost:3000")
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(origin)
        valid_origin = (
            parts.scheme in {"https", "http"}
            and bool(parts.hostname)
            and parts.path in {"", "/"}
            and not (parts.query or parts.fragment or parts.username or parts.password)
            and parts.port != 0
        )
    except ValueError:
        valid_origin = False
    if not valid_origin or (environment == "production" and not origin.startswith("https://")):
        raise SettingsError("RISKLAB_ORIGIN must be a trusted origin (HTTPS in production)")
    if environment == "production" and url is None:
        raise SettingsError("RISKLAB_DATABASE_URL is required in production")

    def optional_path(name: str) -> Path | None:
        value = env.get(name)
        return Path(value) if value else None

    try:
        return Settings(
            environment=environment,
            database_url=SecretStr(url) if url else None,
            storage_root=Path(env.get("RISKLAB_STORAGE_ROOT", "data/application/objects")),
            pool_size=int(env.get("RISKLAB_DB_POOL_SIZE", "3")),
            pool_timeout=int(env.get("RISKLAB_DB_POOL_TIMEOUT", "5")),
            origin=origin.rstrip("/"),
            catalog_file=optional_path("RISKLAB_CATALOG_FILE"),
            catalog_sha256=env.get("RISKLAB_CATALOG_SHA256") or None,
            release_file=optional_path("RISKLAB_RELEASE_FILE"),
            model_file=optional_path("RISKLAB_MODEL_FILE"),
            comparison_file=optional_path("RISKLAB_COMPARISON_FILE"),
            config_dir=Path(env.get("RISKLAB_CONFIG_DIR", "configs")),
            root=Path(env.get("RISKLAB_APP_ROOT", ".")),
        )
    except (ValueError, ValidationError):
        raise SettingsError("Invalid application setting; check numeric limits and paths") from None
