"""baseline: 业务数据与鉴权 PostgreSQL 表结构

Revision ID: 0001
Revises:
Create Date: 2026-08-19

业务集合统一为 (seq bigint PRIMARY KEY, data jsonb NOT NULL)：
完整记录存 data jsonb 保证读写字节级往返，seq 保持 JSON 数组顺序。
鉴权表身份字段建为真实列并冗余 data，便于约束与查询。

注：business 表未建外键，保持与 JSON 时代"孤儿容忍"一致（删除逻辑由
Service 保证）；后续可按需加 FK。评审与规则表数据在 scripts/backfill_postgres.py 回填。
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BUSINESS_TABLES = ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency")
RULE_TABLES = ("rules", "rule_versions", "rule_audit")


def upgrade() -> None:
    for table in BUSINESS_TABLES:
        op.execute(f'CREATE TABLE "{table}" (seq bigint PRIMARY KEY, data jsonb NOT NULL)')

    for table in RULE_TABLES:
        op.execute(f'CREATE TABLE "{table}" (seq bigint PRIMARY KEY, data jsonb NOT NULL)')

    op.execute(
        """CREATE TABLE roles (
            seq bigint PRIMARY KEY,
            code text NOT NULL UNIQUE,
            name text NOT NULL,
            description text
        )"""
    )
    op.execute(
        """CREATE TABLE users (
            id bigint PRIMARY KEY,
            seq bigint NOT NULL UNIQUE,
            username text NOT NULL UNIQUE,
            password_hash text NOT NULL,
            display_name text NOT NULL,
            department text NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            data jsonb NOT NULL
        )"""
    )
    op.execute(
        """CREATE TABLE user_roles (
            user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role_code text NOT NULL REFERENCES roles(code) ON DELETE CASCADE,
            seq bigint NOT NULL,
            PRIMARY KEY (user_id, role_code)
        )"""
    )


def downgrade() -> None:
    for table in (*BUSINESS_TABLES, *RULE_TABLES, "user_roles", "users", "roles"):
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
