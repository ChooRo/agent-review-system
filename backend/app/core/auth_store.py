import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings


class AuthStore:
    """当前 JSON 鉴权存储边界；接数据库时保持方法契约并替换实现。"""

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

    def _hydrate(self, user: dict[str, Any], role_rows: list[dict[str, Any]]) -> dict[str, Any]:
        roles = {role["code"]: role for role in role_rows}
        return {**user, "roles": [roles[code] for code in user.get("role_codes", [])]}


def get_auth_store() -> AuthStore:
    return AuthStore()
