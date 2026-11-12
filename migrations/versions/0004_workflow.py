"""Durable job queue, immutable predictions, reviews and final simulated decisions."""

from alembic import op

revision = "0004_workflow"
down_revision = "0003_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE jobs (
            id uuid PRIMARY KEY,
            kind varchar(16) NOT NULL,
            import_id uuid REFERENCES imports(id),
            application_id uuid REFERENCES applications(id),
            state varchar(16) NOT NULL,
            attempt integer NOT NULL,
            max_attempts integer NOT NULL,
            lease_owner varchar(128),
            lease_expires_at timestamptz,
            available_at timestamptz NOT NULL,
            last_error_code varchar(64),
            retryable boolean NOT NULL,
            initiated_by uuid REFERENCES users(id),
            input_sha256 varchar(64),
            release_identity jsonb,
            created_at timestamptz NOT NULL,
            CONSTRAINT jobs_kind CHECK (kind IN ('IMPORT','SCORING')),
            CONSTRAINT jobs_state CHECK (state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
            CONSTRAINT jobs_attempts CHECK (attempt >= 0 AND max_attempts BETWEEN 1 AND 10),
            CONSTRAINT jobs_target CHECK (
                (kind = 'IMPORT' AND import_id IS NOT NULL AND application_id IS NULL)
                OR (kind = 'SCORING' AND application_id IS NOT NULL AND import_id IS NULL))
        );
        CREATE INDEX jobs_claim ON jobs(state,available_at,created_at,id);
        CREATE INDEX ix_jobs_import_id ON jobs(import_id);
        CREATE INDEX ix_jobs_application_id ON jobs(application_id);
        CREATE UNIQUE INDEX jobs_one_active_import
            ON jobs(import_id) WHERE kind = 'IMPORT' AND state IN ('QUEUED','RUNNING');
        CREATE UNIQUE INDEX jobs_one_active_scoring
            ON jobs(application_id) WHERE kind = 'SCORING' AND state IN ('QUEUED','RUNNING');
        CREATE TABLE predictions (
            id uuid PRIMARY KEY,
            application_id uuid NOT NULL REFERENCES applications(id),
            job_id uuid NOT NULL REFERENCES jobs(id),
            input_revision integer NOT NULL,
            input_sha256 varchar(64) NOT NULL,
            feature_schema_hash varchar(64) NOT NULL,
            model_version varchar(128) NOT NULL,
            model_sha256 varchar(64) NOT NULL,
            model_run_id varchar(128) NOT NULL,
            lock_id varchar(128) NOT NULL,
            feature_contract_version varchar(32) NOT NULL,
            protocol_hashes jsonb NOT NULL,
            probability double precision NOT NULL,
            threshold double precision NOT NULL,
            policy_version varchar(128) NOT NULL,
            sources_used jsonb NOT NULL,
            warnings jsonb NOT NULL,
            explanation jsonb NOT NULL,
            initiated_by uuid REFERENCES users(id),
            created_at timestamptz NOT NULL,
            CONSTRAINT prediction_per_application UNIQUE(application_id),
            CONSTRAINT prediction_input_hash CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT prediction_model_hash CHECK (model_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT prediction_probability CHECK (probability >= 0 AND probability <= 1),
            CONSTRAINT prediction_threshold CHECK (threshold >= 0 AND threshold <= 1)
        );
        CREATE INDEX predictions_created ON predictions(created_at,id);
        CREATE TABLE reviews (
            id uuid PRIMARY KEY,
            application_id uuid NOT NULL REFERENCES applications(id),
            author_id uuid NOT NULL REFERENCES users(id),
            review_number integer NOT NULL,
            prediction_id uuid NOT NULL REFERENCES predictions(id),
            notes text NOT NULL,
            proposal varchar(16),
            submitted_at timestamptz,
            created_at timestamptz NOT NULL,
            CONSTRAINT review_number_per_application UNIQUE(application_id,review_number),
            CONSTRAINT reviews_number CHECK (review_number >= 1),
            CONSTRAINT reviews_proposal CHECK (
                proposal IS NULL OR proposal IN ('APPROVE','DECLINE'))
        );
        CREATE INDEX reviews_application ON reviews(application_id,review_number);
        CREATE TABLE decisions (
            id uuid PRIMARY KEY,
            application_id uuid NOT NULL REFERENCES applications(id),
            review_id uuid NOT NULL REFERENCES reviews(id),
            prediction_id uuid NOT NULL REFERENCES predictions(id),
            decided_by uuid NOT NULL REFERENCES users(id),
            outcome varchar(32) NOT NULL,
            reason varchar(2000) NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT decision_per_application UNIQUE(application_id),
            CONSTRAINT decisions_outcome CHECK (
                outcome IN ('APPROVED_SIMULATED','DECLINED_SIMULATED'))
        );
        CREATE TABLE decision_idempotency (
            actor_id uuid NOT NULL REFERENCES users(id),
            operation varchar(32) NOT NULL,
            application_id uuid NOT NULL REFERENCES applications(id),
            key varchar(128) NOT NULL,
            payload_sha256 varchar(64) NOT NULL,
            decision_id uuid NOT NULL UNIQUE REFERENCES decisions(id),
            status integer NOT NULL,
            response jsonb NOT NULL,
            PRIMARY KEY(actor_id,operation,application_id,key),
            CONSTRAINT decision_idempotency_operation CHECK (operation = 'decisions.create'),
            CONSTRAINT decision_idempotency_key CHECK (key ~ '^[A-Za-z0-9_-]{8,128}$'
                AND payload_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT decision_idempotency_status CHECK (status = 201)
        );
        REVOKE ALL ON jobs, predictions, reviews, decisions, decision_idempotency FROM PUBLIC;
        GRANT SELECT, INSERT ON jobs, predictions, reviews, decisions, decision_idempotency
            TO risklab_runtime;
        GRANT UPDATE(state,attempt,lease_owner,lease_expires_at,available_at,last_error_code,
            retryable) ON jobs TO risklab_runtime;
        GRANT UPDATE(notes,proposal,submitted_at) ON reviews TO risklab_runtime;
        GRANT SELECT, INSERT ON jobs, predictions TO risklab_worker;
        GRANT UPDATE(state,attempt,lease_owner,lease_expires_at,available_at,last_error_code,
            retryable) ON jobs TO risklab_worker;
    """)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is not automatic; restore or roll forward explicitly")
