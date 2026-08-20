import json
from pathlib import Path

from psycopg.types.json import Jsonb

from app.core.config import get_settings
from app.core.security import hash_password


ROLES = [
    {"code": "operator", "name": "业务经办", "description": "发起采购文件审核并处理 AI 候选结论"},
    {"code": "supervisor", "name": "专业监督", "description": "复核采购文件审核结论；采购部门承担主责"},
    {"code": "admin", "name": "系统管理员", "description": "维护账号与系统配置，不确认业务结论"},
]
USERS = [
    (1, "operator", "张明", "采购业务部", "operator"),
    (2, "supervisor", "李华", "采购部门", "supervisor"),
    (3, "admin", "陈启", "信息中心", "admin"),
    (4, "legal_supervisor", "王芳", "法规部门", "supervisor"),
    (5, "finance_supervisor", "赵强", "财务部门", "supervisor"),
    (6, "audit_supervisor", "周敏", "审计部门", "supervisor"),
]
INITIAL_PASSWORD = "ChangeMe123!"


def _seed_postgres(data: dict) -> None:
    from app.repositories.postgres.db import get_pool

    with get_pool().connection() as conn:
        with conn:
            for seq, role in enumerate(data["roles"]):
                conn.execute(
                    "INSERT INTO roles (seq, code, name, description) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description",
                    (seq, role["code"], role["name"], role.get("description")),
                )
            for seq, user in enumerate(data["users"]):
                conn.execute(
                    "INSERT INTO users (id, seq, username, password_hash, display_name, department, is_active, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                    (user["id"], seq, user["username"], user["password_hash"], user["display_name"],
                     user["department"], user.get("is_active", True), Jsonb(user)),
                )
                conn.execute("DELETE FROM user_roles WHERE user_id = %s", (user["id"],))
                for role_seq, code in enumerate(user.get("role_codes", [])):
                    conn.execute("INSERT INTO user_roles (user_id, role_code, seq) VALUES (%s, %s, %s)",
                                 (user["id"], code, role_seq))


def init_auth_data(force: bool = False) -> Path:
    settings = get_settings()
    path = Path(settings.auth_data_file)
    if settings.storage_backend == "postgres":
        data = {
            "version": 1,
            "roles": ROLES,
            "users": [
                {
                    "id": user_id,
                    "username": username,
                    "display_name": display_name,
                    "department": department,
                    "password_hash": hash_password(INITIAL_PASSWORD),
                    "is_active": True,
                    "role_codes": [role_code],
                }
                for user_id, username, display_name, department, role_code in USERS
            ],
        }
        _seed_postgres(data)
        return path
    if path.exists() and not force:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "roles": ROLES,
        "users": [
            {
                "id": user_id,
                "username": username,
                "display_name": display_name,
                "department": department,
                "password_hash": hash_password(INITIAL_PASSWORD),
                "is_active": True,
                "role_codes": [role_code],
            }
            for user_id, username, display_name, department, role_code in USERS
        ],
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


if __name__ == "__main__":
    print(f"鉴权 JSON 已初始化：{init_auth_data()}")
    print("账号：operator / supervisor / admin；初始密码：ChangeMe123!")
