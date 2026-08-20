from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "厦门烟草采购文件智审 API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    auth_data_file: str = "./data/auth.json"
    data_dir: str = "./data"
    uploads_dir: str = "./data/uploads"
    max_upload_bytes: int = 50 * 1024 * 1024
    storage_backend: str = "json"
    database_url: str = ""
    mineru_api_url: str = "http://127.0.0.1:8001"
    mineru_timeout_seconds: int = 900
    review_task_timeout_seconds: int = 120
    jwt_secret: str = "development-only-secret-change-before-deploy"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", enable_decoding=False)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("storage_backend")
    @classmethod
    def storage_backend_supported(cls, value: str) -> str:
        if value not in {"json", "postgres"}:
            raise ValueError("STORAGE_BACKEND 仅支持 json 或 postgres")
        return value



@lru_cache
def get_settings() -> Settings:
    return Settings()
