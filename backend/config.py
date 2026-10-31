"""应用配置。

端口、数据目录、AI 凭据全部来自环境变量或 .env，不在代码里硬编码
（Blueprint 6.4 要求端口写入配置；9.1 要求 API Key 只从环境变量读取）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="PKOS_",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    frontend_port: int = 5173

    data_dir: Path | None = None

    ai_provider: str = ""
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout_seconds: int = 60

    log_level: str = "INFO"
    allow_missing_frontend: bool = True

    @field_validator("data_dir", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        # .env 里 PKOS_DATA_DIR= 留空时应回落到默认目录，而不是被解析成 Path(".")
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @property
    def resolved_data_dir(self) -> Path:
        return Path(self.data_dir) if self.data_dir else PROJECT_ROOT / "data"

    @property
    def backup_dir(self) -> Path:
        return self.resolved_data_dir / "backups"

    @property
    def database_path(self) -> Path:
        return self.resolved_data_dir / "app.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def frontend_dist(self) -> Path:
        return PROJECT_ROOT / "frontend" / "dist"

    def ensure_runtime_dirs(self) -> None:
        self.resolved_data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
