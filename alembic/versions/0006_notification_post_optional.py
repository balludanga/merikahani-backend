"""A follow notification is about a person, so notifications.post_id may be empty."""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {col["name"]: col for col in sa.inspect(op.get_bind()).get_columns("notifications")}
    if not columns["post_id"]["nullable"]:
        # batch mode rebuilds the table on SQLite; on Postgres it is a plain ALTER.
        with op.batch_alter_table("notifications") as batch:
            batch.alter_column("post_id", existing_type=sa.Integer(), nullable=True)
    for name, column in (
        ("ix_notifications_id", "id"),
        ("ix_notifications_user_id", "user_id"),
        ("ix_notifications_post_id", "post_id"),
        ("ix_notifications_created_at", "created_at"),
    ):
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {name} ON notifications ({column})"))


def downgrade() -> None:
    pass
