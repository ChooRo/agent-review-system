"""将法规元数据、版本和条款迁入 PostgreSQL。"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE legal_documents (
            document_key text PRIMARY KEY,
            schema_version text NOT NULL DEFAULT '1.0.0',
            metadata jsonb NOT NULL DEFAULT '{}',
            title text NOT NULL,
            canonical_title text,
            issuer text,
            status text NOT NULL DEFAULT 'unknown',
            effective_date text,
            expiry_date text,
            department text,
            document_version text NOT NULL DEFAULT 'unknown',
            applicable_scope text NOT NULL DEFAULT '',
            metadata_version integer NOT NULL DEFAULT 1,
            quality jsonb NOT NULL DEFAULT '{}',
            metadata_extraction jsonb NOT NULL DEFAULT '{}',
            metadata_history jsonb NOT NULL DEFAULT '[]',
            document_json jsonb NOT NULL DEFAULT '{}',
            source_storage_key text,
            content_fingerprint text,
            topic_vocabulary_version text,
            updated_at timestamptz NOT NULL,
            updated_by bigint
        );
        CREATE TABLE legal_document_versions (
            document_key text NOT NULL REFERENCES legal_documents(document_key) ON DELETE CASCADE,
            metadata_version integer NOT NULL,
            updated_at timestamptz NOT NULL,
            updated_by bigint,
            snapshot jsonb NOT NULL,
            PRIMARY KEY (document_key, metadata_version)
        );
        CREATE TABLE legal_units (
            document_key text NOT NULL REFERENCES legal_documents(document_key) ON DELETE CASCADE,
            legal_unit_id text NOT NULL,
            ordinal integer NOT NULL,
            article_no text,
            article_index integer,
            status text NOT NULL DEFAULT 'unknown',
            effective_date text,
            data jsonb NOT NULL,
            PRIMARY KEY (document_key, legal_unit_id),
            UNIQUE (document_key, ordinal)
        );
        CREATE INDEX ix_legal_documents_status_title ON legal_documents(status, title);
        CREATE INDEX ix_legal_units_document_article ON legal_units(document_key, article_index, ordinal);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS legal_units CASCADE")
    op.execute("DROP TABLE IF EXISTS legal_document_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS legal_documents CASCADE")
