"""把 backend/data 下的 JSON 数据一次性回填到 PostgreSQL。

用法（表结构需先由 alembic upgrade head 建好）：
  cd backend
  $env:STORAGE_BACKEND='postgres'
  $env:DATABASE_URL='postgresql://postgres:postgres@127.0.0.1:5432/xiamen_tobacco'
  uv run --no-sync python scripts/backfill_postgres.py

评审业务数据通过 PostgresReviewRepository.commit 走与运行时完全相同的
JSON→关系表映射（tasks.payload、documents、task_members、project_archive_items
均由此派生）；规则与鉴权表分别回填。脚本会清空并重建业务、规则与鉴权表。
"""

from __future__ import annotations

import json
from pathlib import Path

from psycopg.types.json import Jsonb

from app.core.config import get_settings
from app.repositories.backend import get_review_repository
from app.repositories.postgres.db import get_pool

RULE_TABLES = {"rules": "rules", "versions": "rule_versions", "audit": "rule_audit"}


def main() -> None:
    settings = get_settings()
    if settings.storage_backend != "postgres":
        raise SystemExit("请先设置 STORAGE_BACKEND=postgres 与 DATABASE_URL")
    data_dir = Path(__file__).resolve().parents[1] / "data"

    review = json.loads((data_dir / "review_data.json").read_text(encoding="utf-8"))
    state = {name: review.get(name, []) for name in ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency")}
    repo = get_review_repository(None)
    repo.commit(state)
    for name in ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency"):
        print(f"review {name}: {len(review.get(name, []))}")

    with get_pool().connection() as conn:
        with conn:
            rules_path = data_dir / "rules.json"
            rules = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.is_file() else {}
            for key, table in RULE_TABLES.items():
                conn.execute(f'DELETE FROM "{table}"')
                for seq, item in enumerate(rules.get(key, [])):
                    conn.execute(f'INSERT INTO "{table}" (seq, data) VALUES (%s, %s)', (seq, Jsonb(item)))
                print(f"rule {key}: {len(rules.get(key, []))}")

            auth = json.loads((data_dir / "auth.json").read_text(encoding="utf-8"))
            conn.execute("DELETE FROM user_roles")
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM roles")
            for seq, role in enumerate(auth.get("roles", [])):
                conn.execute(
                    "INSERT INTO roles (seq, code, name, description) VALUES (%s, %s, %s, %s)",
                    (seq, role["code"], role["name"], role.get("description")),
                )
            for seq, user in enumerate(auth.get("users", [])):
                conn.execute(
                    "INSERT INTO users (id, seq, username, password_hash, display_name, department, is_active, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (user["id"], seq, user["username"], user["password_hash"], user["display_name"],
                     user["department"], user.get("is_active", True), Jsonb(user)),
                )
                for role_seq, code in enumerate(user.get("role_codes", [])):
                    conn.execute("INSERT INTO user_roles (user_id, role_code, seq) VALUES (%s, %s, %s)",
                                 (user["id"], code, role_seq))
            print(f"auth roles: {len(auth.get('roles', []))}, users: {len(auth.get('users', []))}")

    print("回填完成。")


if __name__ == "__main__":
    main()
