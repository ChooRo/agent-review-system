from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "厦门烟草采购文件智审 API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    auth_data_file: str = "./data/auth.json"
    data_dir: str = "./data"
    uploads_dir: str = "./data/uploads"
    max_upload_bytes: int = 50 * 1024 * 1024
    storage_backend: str = "postgres"
    database_url: str = ""
    mineru_api_url: str = "http://127.0.0.1:8001"
    mineru_timeout_seconds: int = 900
    review_task_timeout_seconds: int = 120
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str | None = None
    celery_queue: str = "review"
    celery_task_time_limit_seconds: int = Field(default=3600, ge=1)
    celery_task_soft_time_limit_seconds: int = Field(default=3300, ge=1)
    celery_task_max_retries: int = Field(default=3, ge=0)
    celery_task_retry_backoff_max_seconds: int = Field(default=600, ge=1)
    review_heartbeat_interval_seconds: int = Field(default=30, ge=1)
    review_worker_lease_seconds: int = Field(default=180, ge=1)
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

    @model_validator(mode="after")
    def celery_timeouts_are_ordered(self) -> "Settings":
        if self.celery_task_soft_time_limit_seconds >= self.celery_task_time_limit_seconds:
            raise ValueError("CELERY_TASK_SOFT_TIME_LIMIT_SECONDS 必须小于 CELERY_TASK_TIME_LIMIT_SECONDS")
        if self.review_heartbeat_interval_seconds >= self.review_worker_lease_seconds:
            raise ValueError("REVIEW_HEARTBEAT_INTERVAL_SECONDS 必须小于 REVIEW_WORKER_LEASE_SECONDS")
        return self



@lru_cache
def get_settings() -> Settings:
    return Settings()
