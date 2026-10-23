"""server_errors: a short log of requests that ended in a server error (owner's status page)."""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "server_errors" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "server_errors",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("method", sa.String(10), nullable=False),
            sa.Column("path", sa.String(300), nullable=False),
            sa.Column("status", sa.Integer(), nullable=False),
            sa.Column("error", sa.String(300), nullable=True),
        )
    for name, column in (("ix_server_errors_id", "id"), ("ix_server_errors_created_at", "created_at")):
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {name} ON server_errors ({column})"))


def downgrade() -> None:
    pass
