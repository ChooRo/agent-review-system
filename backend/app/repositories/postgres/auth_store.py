"""鉴权存储的 PostgreSQL 实现；契约与 JSON 版一致，依赖方零改动。

users.data 保留完整原始记录（含 role_codes），roles 由 user_roles 关联表
冗余维护以便后续做约束；读取侧按 data 中的 role_codes 顺序还原角色。
"""

from __future__ import annotations

from typing import Any

from app.repositories.postgres.db import transaction


def _role_map(conn) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT code, name, description FROM roles ORDER BY seq").fetchall()
    return {row["code"]: dict(row) for row in rows}


def _hydrate(conn, user: dict[str, Any]) -> dict[str, Any]:
    roles = _role_map(conn)
    return {**user, "roles": [roles[code] for code in user.get("role_codes", []) if code in roles]}


def _select_user(conn, where: str, value: Any) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT data FROM users WHERE {where}", (value,)).fetchone()
    return _hydrate(conn, row["data"]) if row else None


class PostgresAuthStore:
    """当前鉴权存储边界；保持方法契约并替换实现。"""

    def _read(self) -> dict[str, Any]:
        """JSON 版契约的全量读取；服务层 create_task 用它选主责/协办监督。"""
        with transaction() as conn:
            roles = [dict(row) for row in conn.execute("SELECT code, name, description FROM roles ORDER BY seq")]
            users = [_hydrate(conn, row["data"]) for row in conn.execute("SELECT data FROM users ORDER BY seq")]
            return {"roles": roles, "users": users}

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with transaction() as conn:
            return _select_user(conn, "username = %s", username)

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with transaction() as conn:
            return _select_user(conn, "id = %s", user_id)

    def list_roles(self) -> list[dict[str, str]]:
        with transaction() as conn:
            return [{"code": row["code"], "name": row["name"]} for row in conn.execute("SELECT code, name FROM roles ORDER BY seq")]

    def list_active_users(self) -> list[dict[str, Any]]:
        with transaction() as conn:
            return [_hydrate(conn, row["data"]) for row in conn.execute("SELECT data FROM users WHERE is_active ORDER BY seq")]
