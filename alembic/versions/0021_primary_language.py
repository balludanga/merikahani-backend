"""users.primary_language: the one language a person prefers when they read in several."""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "primary_language" not in columns:
        op.add_column("users", sa.Column("primary_language", sa.String(8), nullable=True))


def downgrade() -> None:
    pass
