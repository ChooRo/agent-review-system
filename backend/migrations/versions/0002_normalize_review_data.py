"""将评审业务从集合 JSON 拆为可约束的关系表。"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency"):
        op.execute(f'ALTER TABLE "{table}" RENAME TO "legacy_{table}"')

    op.execute("""
        CREATE TABLE projects (
            id text PRIMARY KEY, name text NOT NULL, project_code text NOT NULL,
            handling_department text NOT NULL, project_owner text NOT NULL,
            project_owner_id bigint, status text NOT NULL, created_by bigint NOT NULL,
            created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            version integer NOT NULL DEFAULT 1,
            CONSTRAINT uq_projects_code UNIQUE (project_code)
        );
        CREATE TABLE project_archive_items (
            project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            item_order integer NOT NULL, value jsonb NOT NULL,
            PRIMARY KEY (project_id, item_order)
        );
        CREATE TABLE tasks (
            id text PRIMARY KEY, project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title text NOT NULL, status text NOT NULL, operator_id bigint,
            engine_run_id text, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            version integer NOT NULL DEFAULT 1, progress numeric(5,2) NOT NULL DEFAULT 0,
            execution_mode text, quality jsonb, legal_facts jsonb, legal_applicability jsonb,
            legal_context_freeze jsonb, legal_applicability_confirmations jsonb,
            pipeline_status text, degraded_steps jsonb, system_warnings jsonb, coverage_matrix jsonb,
            error text, payload jsonb NOT NULL DEFAULT '{}'
        );
        CREATE INDEX ix_tasks_project_id ON tasks(project_id);
        CREATE TABLE documents (
            id text NOT NULL, task_id text NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            file_name text NOT NULL, content_type text NOT NULL, size bigint NOT NULL,
            sha256 text NOT NULL, path text NOT NULL, version integer NOT NULL,
            uploaded_by bigint NOT NULL, uploaded_at timestamptz NOT NULL,
            PRIMARY KEY (task_id, id), UNIQUE (task_id, version)
        );
        CREATE TABLE task_members (
            task_id text NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id bigint NOT NULL, task_role text NOT NULL, department text NOT NULL,
            module_scope jsonb NOT NULL DEFAULT '[]', PRIMARY KEY (task_id, user_id, task_role)
        );
        CREATE TABLE findings (
            id text PRIMARY KEY, task_id text NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            source_type text, risk_level text NOT NULL, title text NOT NULL,
            description text NOT NULL, suggestion text, document_version integer,
            rectification_status text, rectification_version integer, version integer NOT NULL DEFAULT 1,
            payload jsonb NOT NULL DEFAULT '{}'
        );
        CREATE INDEX ix_findings_task_id ON findings(task_id);
        CREATE TABLE comments (
            id text PRIMARY KEY, task_id text NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            finding_id text NOT NULL REFERENCES findings(id) ON DELETE CASCADE, author_id bigint NOT NULL,
            department text NOT NULL, comment text NOT NULL, version integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
        );
        CREATE TABLE events (
            id text PRIMARY KEY, task_id text NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            actor_id bigint NOT NULL, at timestamptz NOT NULL, before_status text,
            after_status text NOT NULL, reason text NOT NULL
        );
        CREATE TABLE audit (
            id text PRIMARY KEY, actor_id text NOT NULL, action text NOT NULL,
            target_id text NOT NULL, at timestamptz NOT NULL, details jsonb NOT NULL DEFAULT '{}'
        );
        CREATE TABLE idempotency (
            key text PRIMARY KEY, response jsonb NOT NULL
        );
    """)

    op.execute("""
        INSERT INTO projects
        SELECT data->>'id', data->>'name', data->>'project_code', data->>'handling_department',
               data->>'project_owner', NULLIF(data->>'project_owner_id','')::bigint,
               data->>'status', (data->>'created_by')::bigint, (data->>'created_at')::timestamptz,
               (data->>'updated_at')::timestamptz, COALESCE((data->>'version')::int, 1)
        FROM legacy_projects;
        INSERT INTO project_archive_items
        SELECT p.id, x.ordinality - 1, x.value
        FROM legacy_projects l JOIN projects p ON p.id = l.data->>'id'
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(l.data->'archive_index','[]')) WITH ORDINALITY x;
        INSERT INTO tasks
        SELECT data->>'id', data->>'project_id', data->>'title', data->>'status',
               NULLIF(data->>'operator_id','')::bigint, data->>'engine_run_id',
               (data->>'created_at')::timestamptz, (data->>'updated_at')::timestamptz,
               COALESCE((data->>'version')::int,1), COALESCE((data->>'progress')::numeric,0),
               data->>'execution_mode', data->'quality', data->'legal_facts', data->'legal_applicability',
               data->'legal_context_freeze', data->'legal_applicability_confirmations', data->>'pipeline_status',
               data->'degraded_steps', data->'system_warnings', data->'coverage_matrix', data->>'error',
               data - ARRAY['id','project_id','title','status','operator_id','engine_run_id','created_at','updated_at','version','progress','execution_mode','quality','legal_facts','legal_applicability','legal_context_freeze','legal_applicability_confirmations','pipeline_status','degraded_steps','system_warnings','coverage_matrix','error','document','document_versions','members']
        FROM legacy_tasks;
        INSERT INTO documents
        SELECT d.value->>'id', t.id, d.value->>'file_name', d.value->>'content_type',
               (d.value->>'size')::bigint, d.value->>'sha256', d.value->>'path',
               COALESCE((d.value->>'version')::int,1), (d.value->>'uploaded_by')::bigint,
               (d.value->>'uploaded_at')::timestamptz
        FROM legacy_tasks l JOIN tasks t ON t.id = l.data->>'id'
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN l.data ? 'document_versions' THEN l.data->'document_versions' ELSE jsonb_build_array(l.data->'document') END
        ) d(value) WHERE d.value IS NOT NULL AND d.value <> 'null'::jsonb;
        INSERT INTO task_members
        SELECT t.id, (m.value->>'user_id')::bigint, m.value->>'task_role', m.value->>'department',
               COALESCE(m.value->'module_scope','[]')
        FROM legacy_tasks l JOIN tasks t ON t.id = l.data->>'id'
        CROSS JOIN LATERAL jsonb_array_elements(COALESCE(l.data->'members','[]')) m(value);
        INSERT INTO findings
        SELECT data->>'id', data->>'task_id', data->>'source_type', data->>'risk_level', data->>'title',
               data->>'description', data->>'suggestion', NULLIF(data->>'document_version','')::int,
               data->>'rectification_status', NULLIF(data->>'rectification_version','')::int,
               COALESCE((data->>'version')::int,1), data - ARRAY['id','task_id','source_type','risk_level','title','description','suggestion','document_version','rectification_status','rectification_version','version']
        FROM legacy_findings;
        INSERT INTO comments SELECT data->>'id', data->>'task_id', data->>'finding_id', (data->>'author_id')::bigint,
               data->>'department', data->>'comment', COALESCE((data->>'version')::int,1),
               (data->>'created_at')::timestamptz, (data->>'updated_at')::timestamptz FROM legacy_comments;
        INSERT INTO events SELECT data->>'id', data->>'task_id', (data->>'actor_id')::bigint,
               (data->>'at')::timestamptz, data->>'before_status', data->>'after_status', data->>'reason' FROM legacy_events;
        INSERT INTO audit SELECT data->>'id', data->>'actor_id', data->>'action', data->>'target_id',
               (data->>'at')::timestamptz, COALESCE(data->'details','{}') FROM legacy_audit;
        INSERT INTO idempotency SELECT data->>'key', data->'response' FROM legacy_idempotency;
    """)


def downgrade() -> None:
    for table in ("idempotency", "audit", "events", "comments", "findings", "task_members", "documents", "tasks", "project_archive_items", "projects"):
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
    for table in ("projects", "tasks", "findings", "comments", "events", "audit", "idempotency"):
        op.execute(f'ALTER TABLE "legacy_{table}" RENAME TO "{table}"')
