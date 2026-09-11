"""Service-owned configuration for the Agentic RAG application."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, ValidationError


class ConfigurationError(RuntimeError):
    """A credential or connection setting is absent or invalid."""

    def __init__(self, settings: tuple[str, ...], *, missing: bool = True):
        self.setting_names = tuple(sorted(settings))
        self.missing = missing
        prefix = "Missing setting" if missing else "Invalid setting"
        super().__init__(", ".join(f"{prefix}: {name}" for name in self.setting_names))


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str = Field(default="127.0.0.1", validation_alias="HOST")
    port: int = Field(default=8000, ge=1, le=65535, validation_alias="PORT")
    openviking_base_url: AnyHttpUrl = Field(
        default="http://127.0.0.1:1933",
        validation_alias="OPENVIKING_BASE_URL",
    )
    openviking_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENVIKING_API_KEY",
    )
    deepseek_base_url: AnyHttpUrl = Field(
        default="https://api.deepseek.com/anthropic",
        validation_alias="DEEPSEEK_BASE_URL",
    )
    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="DEEPSEEK_API_KEY",
    )
    deepseek_model: str = Field(
        default="deepseek-flash",
        validation_alias="DEEPSEEK_MODEL",
    )
    session_database_path: Path = Field(
        default=Path("data/sessions.sqlite3"),
        validation_alias="SESSION_DATABASE_PATH",
    )

    def safe_summary(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "credentials": {
                "DEEPSEEK_API_KEY": self._has_secret(self.deepseek_api_key),
                "OPENVIKING_API_KEY": self._has_secret(self.openviking_api_key),
            },
        }

    @staticmethod
    def _has_secret(secret: SecretStr | None) -> bool:
        return secret is not None and secret.get_secret_value().strip() != ""

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        env_file: Path | str | None = None,
    ) -> "Settings":
        values = dict(os.environ if environ is None else environ)
        selected_file = Path.cwd() / ".env" if env_file is None else Path(env_file)
        file_values = dotenv_values(selected_file)
        for name, value in file_values.items():
            if value is not None and name not in values:
                values[name] = value

        accepted_names = {
            field.validation_alias or name for name, field in cls.model_fields.items()
        }
        constructor_values = {
            name: value for name, value in values.items() if name in accepted_names
        }
        try:
            return cls(**constructor_values)
        except ValidationError as error:
            missing_settings: list[str] = []
            invalid_settings: list[str] = []
            for item in error.errors():
                setting_name = ".".join(str(part) for part in item["loc"])
                if item["type"] == "missing":
                    missing_settings.append(setting_name)
                else:
                    invalid_settings.append(setting_name)
            if missing_settings:
                raise ConfigurationError(tuple(missing_settings), missing=True) from None
            raise ConfigurationError(tuple(invalid_settings), missing=False) from None




