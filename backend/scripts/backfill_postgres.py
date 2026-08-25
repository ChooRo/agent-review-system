"""把 backend/data 下的 JSON 数据一次性回填到 PostgreSQL。

用法（表结构需先由 alembic upgrade head 建好）：
  cd backend
  $env:STORAGE_BACKEND='postgres'
  $env:DATABASE_URL='postgresql://postgres:postgres@127.0.0.1:5432/xiamen_tobacco'
  uv run --no-sync python scripts/backfill_postgres.py

评审业务数据通过 PostgresReviewRepository.commit 走与运行时完全相同的
JSON→关系表映射（tasks.payload、documents、task_members、project_archive_items
均由此派生）；规则与鉴权表分别按主键回填。脚本不会触碰备份或上传目录。
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from psycopg.types.json import Jsonb

from app.core.config import get_settings
from app.repositories.postgres.db import transaction
from app.repositories.postgres.knowledge_repository import PostgresKnowledgeRepository
from app.repositories.postgres.review_repository import PostgresReviewRepository
from app.repositories.postgres.rule_repository import PostgresRuleRepository
from app.integrations.storage.local import LocalStorage

RULE_TABLES = {"rules": "rules", "versions": "rule_versions", "audit": "rule_audit"}


def backfill_legal_knowledge(root: Path, repository: PostgresKnowledgeRepository, storage: LocalStorage | None = None) -> int:
    """幂等回填历史法规 JSON；仅此迁移入口读取 knowledge/rules。"""
    count = 0
    for path in sorted(root.glob("*/legal_knowledge.json")) if root.is_dir() else []:
        value = json.loads(path.read_text(encoding="utf-8"))
        key = str(value.get("legal_document", {}).get("document_key") or path.parent.name)
        source = next(iter(path.parent.glob("original.*")), None)
        storage_key = None
        if source and source.is_file():
            storage_key = f"legal/{key}/{source.name}"
            if storage:
                storage.upload(storage_key, source.read_bytes())
        document_path = path.parent / "document.json"
        document = json.loads(document_path.read_text(encoding="utf-8")) if document_path.is_file() else {"blocks": []}
        repository.upsert_legacy(value, document, storage_key, "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())
        count += 1
    return count


def main() -> None:
    settings = get_settings()
    if settings.storage_backend != "postgres":
        raise SystemExit("请先设置 STORAGE_BACKEND=postgres 与 DATABASE_URL")
    data_dir = Path(__file__).resolve().parents[1] / "data"

    review = json.loads((data_dir / "review_data.json").read_text(encoding="utf-8"))
    state = {name: review.get(name, []) for name in ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency")}
    repo = PostgresReviewRepository(None)
    repo.commit(state)
    for name in ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency"):
        print(f"review {name}: {len(review.get(name, []))}")

    rules_path = data_dir / "rules.json"
    if rules_path.is_file():
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        PostgresRuleRepository(data_dir).transaction(lambda state: state.update({key: rules.get(key, []) for key in RULE_TABLES}))
        for key in RULE_TABLES:
            print(f"rule {key}: {len(rules.get(key, []))}")
    else:
        print("rule: 未找到 rules.json，保留数据库现有规则")

    auth = json.loads((data_dir / "auth.json").read_text(encoding="utf-8"))
    with transaction() as conn:
        role_codes = {role["code"] for role in auth.get("roles", [])}
        user_ids = {user["id"] for user in auth.get("users", [])}
        for seq, role in enumerate(auth.get("roles", [])):
            conn.execute(
                "INSERT INTO roles (seq, code, name, description) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (code) DO UPDATE SET seq=EXCLUDED.seq, name=EXCLUDED.name, description=EXCLUDED.description",
                (seq, role["code"], role["name"], role.get("description")),
            )
        for seq, user in enumerate(auth.get("users", [])):
            conn.execute(
                "INSERT INTO users (id, seq, username, password_hash, display_name, department, is_active, data) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET seq=EXCLUDED.seq, username=EXCLUDED.username, password_hash=EXCLUDED.password_hash, "
                "display_name=EXCLUDED.display_name, department=EXCLUDED.department, is_active=EXCLUDED.is_active, data=EXCLUDED.data",
                (user["id"], seq, user["username"], user["password_hash"], user["display_name"], user["department"], user.get("is_active", True), Jsonb(user)),
            )
            conn.execute("DELETE FROM user_roles WHERE user_id=%s", (user["id"],))
            for role_seq, code in enumerate(user.get("role_codes", [])):
                conn.execute("INSERT INTO user_roles (user_id, role_code, seq) VALUES (%s, %s, %s) ON CONFLICT (user_id, role_code) DO UPDATE SET seq=EXCLUDED.seq", (user["id"], code, role_seq))
        for row in conn.execute("SELECT id FROM users"):
            if row["id"] not in user_ids:
                conn.execute("DELETE FROM users WHERE id=%s", (row["id"],))
        for row in conn.execute("SELECT code FROM roles"):
            if row["code"] not in role_codes:
                conn.execute("DELETE FROM roles WHERE code=%s", (row["code"],))
    print(f"auth roles: {len(auth.get('roles', []))}, users: {len(auth.get('users', []))}")

    knowledge_root = Path(__file__).resolve().parents[2] / "knowledge" / "rules"
    knowledge_repo = PostgresKnowledgeRepository(data_dir, LocalStorage(settings.uploads_dir))
    legal_count = backfill_legal_knowledge(knowledge_root, knowledge_repo, knowledge_repo.storage)
    for value in knowledge_repo.list_knowledge():
        print(f"legal {value['legal_document']['document_key']}: {len(value.get('units', []))} units")
    print(f"legal documents: {legal_count}")

    print("回填完成。")


if __name__ == "__main__":
    main()
