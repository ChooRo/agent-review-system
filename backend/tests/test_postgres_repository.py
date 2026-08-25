"""PostgreSQL Repository 测试。

默认跳过：仅当 STORAGE_BACKEND=postgres 且 DATABASE_URL 可连接时运行，
避免污染常规 JSON 开发/测试环境。运行方式：

  cd backend
  $env:STORAGE_BACKEND='postgres'
  $env:DATABASE_URL='postgresql://postgres:postgres@127.0.0.1:5432/xiamen_tobacco_test'
  uv run --no-sync pytest tests/test_postgres_repository.py -q

表结构需已由 `uv run --no-sync alembic upgrade head` 建好；测试前后清空相关表。
"""

from __future__ import annotations

import threading

import pytest
from psycopg import connect

from app.core.auth_store import get_auth_store
from app.core.config import get_settings
from app.repositories.postgres.review_repository import PostgresReviewRepository
from app.repositories.postgres.db import transaction

COLLECTIONS = ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency")
RULE_TABLES = ("rules", "rule_versions", "rule_audit")


def _postgres_reachable() -> bool:
    try:
        with connect(get_settings().database_url, connect_timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    get_settings().storage_backend != "postgres" or not _postgres_reachable(),
    reason="需要 STORAGE_BACKEND=postgres 且 DATABASE_URL 可连接",
)


@pytest.fixture(autouse=True)
def clean_tables():
    def _truncate() -> None:
        with transaction() as conn:
            conn.execute(
                "TRUNCATE projects, project_archive_items, tasks, documents, task_members, findings, comments, "
                "events, audit, idempotency, rules, rule_versions, rule_audit, user_roles, users, roles CASCADE"
            )
    _truncate()  # 先清空：关系表带外键，测试不能依赖库里的既有行
    yield
    _truncate()


def _project(item_id: str) -> dict:
    return {"id": item_id, "name": f"项目-{item_id}", "project_code": item_id, "handling_department": "procurement",
            "project_owner": "经办", "status": "draft", "task_ids": [], "archive_index": [],
            "created_by": 1, "created_at": "2026-08-19T00:00:00+00:00", "updated_at": "2026-08-19T00:00:00+00:00",
            "version": 1}


def test_postgres_collection_round_trip(tmp_path) -> None:
    repo = PostgresReviewRepository(tmp_path)
    projects = repo.collection("projects")
    projects.write({"items": [_project("prj_a"), _project("prj_b")]})
    items = repo.collection("projects").read()["items"]
    assert [x["id"] for x in items] == ["prj_a", "prj_b"]
    # data jsonb 字节级往返：嵌套字段原样
    assert items[0]["project_owner"] == "经办"


def test_postgres_collection_write_preserves_unrelated_rows(tmp_path) -> None:
    repo = PostgresReviewRepository(tmp_path)
    repo.collection("projects").write({"items": [_project("prj_a"), _project("prj_b")]})
    repo.collection("tasks").write({"items": [{
        "id": "prt_keep", "project_id": "prj_b", "title": "保留任务", "status": "draft",
        "version": 1, "progress": 0, "created_at": "2026-08-19T00:00:00+00:00",
        "updated_at": "2026-08-19T00:00:00+00:00", "members": [], "document_versions": [],
    }]})
    with transaction() as conn:
        conn.execute(
            "INSERT INTO events (id, task_id, actor_id, at, before_status, after_status, reason) "
            "VALUES ('evt_keep', 'prt_keep', 0, '2026-08-19T00:00:00+00:00', NULL, 'draft', '保留')"
        )
    changed = repo.collection("projects").read()["items"]
    changed[0]["name"] = "更新后的项目"
    repo.collection("projects").write({"items": changed})
    with transaction() as conn:
        assert conn.execute("SELECT count(*) AS count FROM projects").fetchone()["count"] == 2
        assert conn.execute("SELECT reason FROM events WHERE id='evt_keep'").fetchone()["reason"] == "保留"


def test_postgres_transaction_atomic(tmp_path) -> None:
    repo = PostgresReviewRepository(tmp_path)
    repo.collection("projects").write({"items": [_project("prj_a")]})

    def persist(state):
        task = {"id": "prt_1", "project_id": "prj_a", "title": "t", "status": "queued", "version": 1,
                "progress": 0, "created_at": "2026-08-19T00:00:00+00:00", "updated_at": "2026-08-19T00:00:00+00:00"}
        state["tasks"].append(task)
        state["events"].append({"id": "evt_1", "task_id": "prt_1", "actor_id": 1, "at": "2026-08-19T00:00:00+00:00",
                                "before_status": None, "after_status": "queued", "reason": "创建"})

    repo.transaction(persist)
    state = repo.load()
    assert len(state["tasks"]) == 1 and len(state["events"]) == 1


def test_postgres_concurrent_transactions_do_not_lose_updates(tmp_path) -> None:
    repo = PostgresReviewRepository(tmp_path)
    repo.collection("projects").write({"items": [_project("prj_base")]})
    # advisory 锁使读-改-写按集合串行化：第二个事务拿到锁后才读取，因此
    # 必须在锁外起跑，否则持锁等 barrier 会与等锁的事务互相死等。
    start = threading.Barrier(2)

    def add(item_id: str) -> None:
        start.wait()
        def persist(state):
            state["projects"].append(_project(item_id))
        repo.transaction(persist)

    threads = [threading.Thread(target=add, args=(f"prj_{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ids = [x["id"] for x in repo.load()["projects"]]
    assert "prj_base" in ids and "prj_0" in ids and "prj_1" in ids


def test_postgres_auth_store(tmp_path) -> None:
    with transaction() as conn:
        conn.execute("INSERT INTO roles (seq, code, name, description) VALUES (0, 'operator', '业务经办', '经办')")
        conn.execute(
            "INSERT INTO users (id, seq, username, password_hash, display_name, department, is_active, data) "
            "VALUES (7, 0, 'tester', 'x', '测试', '部门', true, '{\"id\":7,\"username\":\"tester\",\"role_codes\":[\"operator\"]}'::jsonb)"
        )
        conn.execute("INSERT INTO user_roles (user_id, role_code, seq) VALUES (7, 'operator', 0)")

    store = get_auth_store()
    user = store.get_user_by_username("tester")
    assert user is not None and user["roles"][0]["name"] == "业务经办"
    assert store.get_user_by_id(7)["username"] == "tester"
    assert store.list_active_users()[0]["username"] == "tester"
    assert store.list_roles() == [{"code": "operator", "name": "业务经办"}]
