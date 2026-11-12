"""Durable bounded-window login attempts, shared by API processes."""

from alembic import op

revision = "0002_login_limits"
down_revision = "0001_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE login_limits (
            key_hash varchar(64) PRIMARY KEY,
            window_start timestamptz NOT NULL,
            attempts integer NOT NULL CHECK (attempts >= 1)
        );
        CREATE INDEX ix_login_limits_window_start ON login_limits(window_start);
        REVOKE ALL ON login_limits FROM PUBLIC;
        GRANT SELECT, INSERT, UPDATE, DELETE ON login_limits TO risklab_runtime;
    """)


def downgrade() -> None:
    raise RuntimeError("Destructive downgrade is not automatic")
