"""revoked_sessions: the sign-ins that were ended by pressing "sign out" (one row per device)."""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "revoked_sessions" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "revoked_sessions",
            sa.Column("sid", sa.String(32), primary_key=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_revoked_sessions_expires_at ON revoked_sessions (expires_at)"))


def downgrade() -> None:
    pass
