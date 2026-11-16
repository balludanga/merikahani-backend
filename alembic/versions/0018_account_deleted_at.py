"""users.deleted_at: set when a person deletes their account (the row stays as an empty shell for the money records)."""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {col["name"] for col in sa.inspect(op.get_bind()).get_columns("users")}
    if "deleted_at" not in columns:
        op.add_column("users", sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    pass
