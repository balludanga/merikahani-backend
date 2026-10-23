"""Highlights remember where they are; responses can quote a passage.

* highlights.start_offset / prefix / suffix  find the passage again after a reload.
* comments.quote                              the passage a response was written about.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _columns(table: str) -> set:
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    highlights = _columns("highlights")
    if "start_offset" not in highlights:
        op.add_column("highlights", sa.Column("start_offset", sa.Integer(), nullable=True))
    if "prefix" not in highlights:
        op.add_column("highlights", sa.Column("prefix", sa.String(80), nullable=True))
    if "suffix" not in highlights:
        op.add_column("highlights", sa.Column("suffix", sa.String(80), nullable=True))
    if "quote" not in _columns("comments"):
        op.add_column("comments", sa.Column("quote", sa.Text(), nullable=True))


def downgrade() -> None:
    pass
