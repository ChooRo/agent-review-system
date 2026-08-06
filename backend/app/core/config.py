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
    jwt_secret: str = "development-only-secret-change-before-deploy"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    cors_origins: list[str] = ["http://localhost:5173"]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("storage_backend")
    @classmethod
    def only_json_storage(cls, value: str) -> str:
        if value != "json":
            raise ValueError("当前仅支持 STORAGE_BACKEND=json；PostgreSQL Repository 尚未实现")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
