"""Let the worker validate import actors and persist scoring state without account authority."""

from alembic import op

revision = "0005_worker_permissions"
down_revision = "0004_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT(id, role, is_active) ON users TO risklab_worker")
    op.execute("GRANT UPDATE(scoring_state) ON applications TO risklab_worker")


def downgrade() -> None:
    op.execute("REVOKE SELECT(id, role, is_active) ON users FROM risklab_worker")
    op.execute("REVOKE UPDATE(scoring_state) ON applications FROM risklab_worker")
