import json
from pathlib import Path

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
]
INITIAL_PASSWORD = "ChangeMe123!"


def init_auth_data(force: bool = False) -> Path:
    path = Path(get_settings().auth_data_file)
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
