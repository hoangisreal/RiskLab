"""Identity and append-only audit foundation; no speculative business tables."""

from alembic import op

revision = "0001_identity"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE users (
            id uuid PRIMARY KEY,
            username varchar(64) NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9_.-]{3,64}$'),
            password_hash text NOT NULL,
            role varchar(16) NOT NULL CHECK (role IN ('ANALYST','MANAGER','ADMIN')),
            is_active boolean NOT NULL DEFAULT true,
            revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE sessions (
            token_hash varchar(64) PRIMARY KEY,
            user_id uuid REFERENCES users(id),
            csrf_hash varchar(64) NOT NULL,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_sessions_user_id ON sessions(user_id);
        CREATE INDEX ix_sessions_expires_at ON sessions(expires_at);
        CREATE TABLE audit_events (
            id uuid PRIMARY KEY,
            actor_id uuid REFERENCES users(id),
            actor_type varchar(16) NOT NULL CHECK (actor_type IN ('USER','WORKER','SYSTEM')),
            action varchar(64) NOT NULL,
            resource_type varchar(32) NOT NULL,
            resource_id uuid NOT NULL,
            request_id uuid,
            job_id uuid,
            reason varchar(2000),
            metadata jsonb NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX audit_resource_time ON audit_events(resource_type,resource_id,created_at,id);
        REVOKE ALL ON users, sessions, audit_events FROM PUBLIC;
        GRANT SELECT, INSERT, UPDATE ON users TO risklab_runtime;
        GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO risklab_runtime;
        GRANT SELECT, INSERT ON audit_events TO risklab_runtime;
        GRANT INSERT ON audit_events TO risklab_worker;
    """)
    # Readiness can check migration identity, but runtime cannot change it or run DDL.
    op.execute("GRANT SELECT ON alembic_version TO risklab_runtime, risklab_worker")


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is not automatic; restore or roll forward explicitly")
