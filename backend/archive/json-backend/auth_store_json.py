"""归档：JSON 鉴权存储实现（已被 Postgres 取代）。

原为 app/core/auth_store.py 内的 AuthStore 类；Postgres 落地后提取到此处归档。
需要恢复 JSON 后端时：把本类放回 core/auth_store.py，并把 get_auth_store()
改回按 STORAGE_BACKEND 分发。
"""

import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings


class AuthStore:
    """JSON 文件鉴权存储；与 PostgresAuthStore 保持同一方法契约。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or get_settings().auth_data_file)

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.is_file():
            raise RuntimeError(f"鉴权数据不存在，请先运行初始化脚本：{self.path}")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        data = self._read()
        return next(
            (self._hydrate(user, data["roles"]) for user in data["users"] if user["username"] == username),
            None,
        )

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        data = self._read()
        return next(
            (self._hydrate(user, data["roles"]) for user in data["users"] if user["id"] == user_id),
            None,
        )

    def list_roles(self) -> list[dict[str, str]]:
        return [{"code": role["code"], "name": role["name"]} for role in self._read()["roles"]]

    def list_active_users(self) -> list[dict[str, Any]]:
        data = self._read()
        return [self._hydrate(user, data["roles"]) for user in data["users"] if user.get("is_active")]

    def _hydrate(self, user: dict[str, Any], role_rows: list[dict[str, Any]]) -> dict[str, Any]:
        roles = {role["code"]: role for role in role_rows}
        return {**user, "roles": [roles[code] for code in user.get("role_codes", [])]}
