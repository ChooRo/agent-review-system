"""测试库守卫 + 每例清空 + 种子账号。

Postgres 是唯一后端后，整个套件都跑在真实表上；tests 里的 TRUNCATE
会清空全部表。因此强制要求 DATABASE_URL 指向 *_test 库，否则拒绝运行，
防止误清线上 xiamen_tobacco 的生产数据。运行方式：

  cd backend
  $env:DATABASE_URL='postgresql://postgres:postgres@127.0.0.1:5432/xiamen_tobacco_test'
  uv run --no-sync pytest -q
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from app.core.config import get_settings
from app.repositories.postgres.db import transaction

ALL_TABLES = (
    "projects, project_archive_items, tasks, documents, task_members, findings, "
    "comments, events, audit, idempotency, rules, rule_versions, rule_audit, "
    "user_roles, users, roles, legal_units, legal_document_versions, legal_documents"
)


def _database_name() -> str:
    return urlparse(get_settings().database_url).path.lstrip("/")


_dbname = _database_name()
if not _dbname.endswith("_test"):
    pytest.exit(
        f"拒绝运行：DATABASE_URL 指向 {_dbname or '(空)'}，不是 *_test 库。"
        f"测试会 TRUNCATE 全部表，请先设置 DATABASE_URL 指向测试库再跑套件。"
    )


def _truncate() -> None:
    with transaction() as conn:
        conn.execute(f"TRUNCATE {ALL_TABLES} CASCADE")


@pytest.fixture(autouse=True)
def _postgres_clean_seed():
    """每个测试前清空全部表并种入账号，测试后清空，不留脏数据。

    种子复用 scripts/init_auth 的生产种子路径（含 password_hash、role_codes），
    保证测试账号与线上一致，不维护第二份种子。
    """
    from scripts.init_auth import init_auth_data
    _truncate()
    init_auth_data()
    yield
    _truncate()
