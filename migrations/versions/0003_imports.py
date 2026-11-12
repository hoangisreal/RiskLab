"""Immutable import intent, source application inputs and durable idempotency results."""

from alembic import op

revision = "0003_imports"
down_revision = "0002_login_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE imports (
            id uuid PRIMARY KEY,
            actor_id uuid NOT NULL REFERENCES users(id),
            catalog_version varchar(64) NOT NULL,
            catalog_sha256 varchar(64) NOT NULL,
            file_sha256 varchar(64) NOT NULL,
            import_contract_version varchar(32) NOT NULL,
            release_identity jsonb NOT NULL,
            state varchar(16) NOT NULL,
            revision integer NOT NULL,
            attempt integer NOT NULL,
            total_rows integer,
            created_count integer NOT NULL,
            ready_count integer NOT NULL,
            incomplete_count integer NOT NULL,
            retryable boolean NOT NULL,
            errors jsonb NOT NULL,
            errors_truncated boolean NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT imports_state CHECK (state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
            CONSTRAINT imports_revision CHECK (revision >= 1 AND attempt >= 1),
            CONSTRAINT imports_hashes CHECK (
                file_sha256 ~ '^[0-9a-f]{64}$' AND catalog_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT imports_rows CHECK (total_rows IS NULL OR total_rows BETWEEN 1 AND 100),
            CONSTRAINT imports_counts CHECK (created_count >= 0 AND ready_count >= 0
                AND incomplete_count >= 0 AND created_count = ready_count + incomplete_count),
            CONSTRAINT imports_completion CHECK ((state = 'SUCCEEDED' AND total_rows IS NOT NULL
                AND created_count = total_rows) OR (state <> 'SUCCEEDED' AND created_count = 0))
        );
        CREATE INDEX imports_actor_state ON imports(actor_id,state);
        CREATE INDEX imports_created ON imports(created_at,id);
        CREATE TABLE applications (
            id uuid PRIMARY KEY,
            import_id uuid NOT NULL REFERENCES imports(id),
            external_reference varchar(64) NOT NULL,
            catalog_version varchar(64) NOT NULL,
            snapshot_id varchar(64) NOT NULL,
            input_sha256 varchar(64) NOT NULL,
            input_revision integer NOT NULL,
            feature_contract_version varchar(32) NOT NULL,
            source_availability jsonb NOT NULL,
            missing_sources jsonb NOT NULL,
            feature_availability varchar(16) NOT NULL,
            scoring_state varchar(16) NOT NULL,
            review_state varchar(32) NOT NULL,
            assignee_id uuid REFERENCES users(id),
            supersedes_application_id uuid REFERENCES applications(id),
            revision integer NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT application_source_identity UNIQUE(import_id,external_reference),
            CONSTRAINT application_references CHECK (external_reference ~ '^[A-Za-z0-9_-]{1,64}$'
                AND snapshot_id ~ '^[A-Za-z0-9_-]{1,64}$'),
            CONSTRAINT application_input_hash CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT application_revision CHECK (input_revision = 1 AND revision >= 1),
            CONSTRAINT application_availability CHECK (
                feature_availability IN ('READY','INCOMPLETE','INVALID')),
            CONSTRAINT application_scoring_state CHECK (
                scoring_state IN ('NOT_REQUESTED','QUEUED','RUNNING','SUCCEEDED','FAILED')),
            CONSTRAINT application_review_state CHECK (
                review_state IN ('UNASSIGNED','ASSIGNED','IN_REVIEW','PENDING_DECISION','DECIDED')),
            CONSTRAINT application_replacement CHECK (
                supersedes_application_id IS NULL OR supersedes_application_id <> id)
        );
        CREATE INDEX ix_applications_import_id ON applications(import_id);
        CREATE INDEX applications_assignee ON applications(assignee_id,created_at,id);
        CREATE INDEX applications_created ON applications(created_at,id);
        CREATE TABLE import_idempotency (
            actor_id uuid NOT NULL REFERENCES users(id),
            operation varchar(32) NOT NULL,
            key varchar(128) NOT NULL,
            payload_sha256 varchar(64) NOT NULL,
            import_id uuid NOT NULL UNIQUE REFERENCES imports(id),
            status integer NOT NULL,
            response jsonb NOT NULL,
            PRIMARY KEY(actor_id,operation,key),
            CONSTRAINT import_idempotency_operation CHECK (operation = 'imports.create'),
            CONSTRAINT import_idempotency_key CHECK (key ~ '^[A-Za-z0-9_-]{8,128}$'
                AND payload_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT import_idempotency_status CHECK (status = 202)
        );
        REVOKE ALL ON imports, applications, import_idempotency FROM PUBLIC;
        GRANT SELECT, INSERT ON imports, applications, import_idempotency TO risklab_runtime;
        GRANT UPDATE(state,revision,attempt,total_rows,created_count,ready_count,
            incomplete_count,retryable,errors,errors_truncated) ON imports TO risklab_runtime;
        GRANT UPDATE(assignee_id,review_state,scoring_state,revision)
            ON applications TO risklab_runtime;
        GRANT SELECT ON imports, applications TO risklab_worker;
        GRANT INSERT ON applications TO risklab_worker;
        GRANT UPDATE(state,revision,attempt,total_rows,created_count,ready_count,
            incomplete_count,retryable,errors,errors_truncated) ON imports TO risklab_worker;
    """)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is not automatic; restore or roll forward explicitly")
