"""tasks 报告列并入 payload，删死列与 legacy_*。

这些字段（quality / legal_facts / legal_applicability / legal_context_freeze /
legal_applicability_confirmations / pipeline_status / degraded_steps /
system_warnings / coverage_matrix / execution_mode）是评审引擎一次生成的报告
输出，整存整取，从不被 SQL 查询/约束/排序，收进 payload jsonb 后 workflow
新增报告字段无需再迁移。execution_mode 虽为死列，但 0002 曾把 JSON 时代旧
数据搬进该列，同样并进 payload 保留，不裸删。
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_REPORT_COLUMNS = (
    "quality", "legal_facts", "legal_applicability", "legal_context_freeze",
    "legal_applicability_confirmations", "pipeline_status", "degraded_steps",
    "system_warnings", "coverage_matrix", "execution_mode",
)


def upgrade() -> None:
    merged = ", ".join(f"'{name}', {name}" for name in _REPORT_COLUMNS)
    op.execute(f"""
        UPDATE tasks SET payload = payload || jsonb_strip_nulls(jsonb_build_object({merged}))
    """)
    op.execute("ALTER TABLE tasks " + ", ".join(f"DROP COLUMN {name}" for name in _REPORT_COLUMNS))
    # legacy_* 是 0002 变换的中间产物，回填后无人读取，删除。
    for table in ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency"):
        op.execute(f'DROP TABLE IF EXISTS "legacy_{table}"')


def downgrade() -> None:
    for name in _REPORT_COLUMNS:
        kind = "jsonb" if name not in ("pipeline_status", "execution_mode") else "text"
        op.execute(f"ALTER TABLE tasks ADD COLUMN {name} {kind}")
    op.execute("""
        UPDATE tasks SET
            quality = payload->'quality', legal_facts = payload->'legal_facts',
            legal_applicability = payload->'legal_applicability',
            legal_context_freeze = payload->'legal_context_freeze',
            legal_applicability_confirmations = payload->'legal_applicability_confirmations',
            pipeline_status = payload->>'pipeline_status', degraded_steps = payload->'degraded_steps',
            system_warnings = payload->'system_warnings', coverage_matrix = payload->'coverage_matrix',
            execution_mode = payload->>'execution_mode'
    """)
    # legacy_* 已被 0002 变换消费，无源数据可还原，不重建。
