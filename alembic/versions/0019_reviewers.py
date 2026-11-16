"""reviewers: addresses the owner added to the review team from the app."""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "reviewers" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "reviewers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("added_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_reviewers_id ON reviewers (id)"))
    op.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ix_reviewers_email ON reviewers (email)"))


def downgrade() -> None:
    pass
