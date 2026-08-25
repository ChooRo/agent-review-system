"""为生产运行补齐行级关系约束、检查约束和查询索引。"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD CONSTRAINT fk_projects_owner FOREIGN KEY (project_owner_id) REFERENCES users(id) ON DELETE SET NULL NOT VALID")
    op.execute("ALTER TABLE tasks ADD CONSTRAINT fk_tasks_operator FOREIGN KEY (operator_id) REFERENCES users(id) ON DELETE SET NULL NOT VALID")
    op.execute("ALTER TABLE documents ADD CONSTRAINT fk_documents_uploader FOREIGN KEY (uploaded_by) REFERENCES users(id) NOT VALID")
    op.execute("ALTER TABLE task_members ADD CONSTRAINT fk_task_members_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT NOT VALID")
    op.execute("ALTER TABLE comments ADD CONSTRAINT fk_comments_author FOREIGN KEY (author_id) REFERENCES users(id) NOT VALID")
    op.execute("ALTER TABLE projects ADD CONSTRAINT ck_projects_version CHECK (version >= 1)")
    op.execute("ALTER TABLE tasks ADD CONSTRAINT ck_tasks_version CHECK (version >= 1)")
    op.execute("ALTER TABLE tasks ADD CONSTRAINT ck_tasks_progress CHECK (progress >= 0 AND progress <= 100)")
    op.execute("ALTER TABLE documents ADD CONSTRAINT ck_documents_version CHECK (version >= 1)")
    op.execute("ALTER TABLE findings ADD CONSTRAINT ck_findings_version CHECK (version >= 1)")
    for statement in (
        "CREATE INDEX ix_projects_created_by ON projects(created_by)",
        "CREATE INDEX ix_projects_status_updated_at ON projects(status, updated_at DESC)",
        "CREATE INDEX ix_tasks_project_status ON tasks(project_id, status)",
        "CREATE INDEX ix_tasks_updated_at ON tasks(updated_at DESC)",
        "CREATE INDEX ix_documents_task_version ON documents(task_id, version DESC)",
        "CREATE INDEX ix_task_members_user ON task_members(user_id)",
        "CREATE INDEX ix_findings_task_version ON findings(task_id, document_version)",
        "CREATE INDEX ix_comments_task_finding ON comments(task_id, finding_id)",
        "CREATE INDEX ix_events_task_at ON events(task_id, at)",
        "CREATE INDEX ix_audit_target_at ON audit(target_id, at DESC)",
        "CREATE INDEX ix_rules_module_status ON rules ((data->>'module'), (data->>'status'))",
        "CREATE INDEX ix_rule_versions_rule_status ON rule_versions ((data->>'id'), (data->>'status'))",
    ):
        op.execute(statement)


def downgrade() -> None:
    for name in (
        "ix_rule_versions_rule_status", "ix_rules_module_status", "ix_audit_target_at", "ix_events_task_at",
        "ix_comments_task_finding", "ix_findings_task_version", "ix_task_members_user", "ix_documents_task_version",
        "ix_tasks_updated_at", "ix_tasks_project_status", "ix_projects_status_updated_at", "ix_projects_created_by",
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")
    for table, name in (
        ("findings", "ck_findings_version"), ("documents", "ck_documents_version"), ("tasks", "ck_tasks_progress"),
        ("tasks", "ck_tasks_version"), ("projects", "ck_projects_version"),
        ("comments", "fk_comments_author"), ("task_members", "fk_task_members_user"),
        ("documents", "fk_documents_uploader"), ("tasks", "fk_tasks_operator"), ("projects", "fk_projects_owner"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
