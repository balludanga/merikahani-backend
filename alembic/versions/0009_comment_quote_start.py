"""A response remembers where in the story its quoted passage starts (comments.quote_start),
so the same sentence appearing twice is found in the right place."""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "quote_start" not in {col["name"] for col in sa.inspect(op.get_bind()).get_columns("comments")}:
        op.add_column("comments", sa.Column("quote_start", sa.Integer(), nullable=True))


def downgrade() -> None:
    pass
