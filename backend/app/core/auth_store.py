"""鉴权存储边界：当前唯一实现为 Postgres。

JSON 版 AuthStore 已归档至 backend/archive/json-backend/auth_store_json.py；
如未来需要恢复双后端，把归档类放回并按 STORAGE_BACKEND 分发即可。
"""

from app.repositories.postgres.auth_store import PostgresAuthStore

# deps.py 用 AuthStore 做类型标注；Postgres 是唯一实现，直接别名。
AuthStore = PostgresAuthStore


def get_auth_store() -> PostgresAuthStore:
    return PostgresAuthStore()
