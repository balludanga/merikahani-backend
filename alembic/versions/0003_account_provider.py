"""Track how an account signs in, and let sessions be revoked.

* auth_provider  'password' | 'google'. Password login is refused for 'google'.
* email_verified True only when Google vouched for the address.
* token_version  bumped to invalidate every token issued before.

Backfill: before this revision a Google sign-up stored a bcrypt hash of the first
72 characters of the Google ID token - the JWT header, identical for every user
signed with the same Google key - so anyone could log in to those accounts with a
public "password". Google sign-ups were the only accounts that carry a Google
avatar (the register form cannot set one), so every such row is retired here:
provider -> google, password -> random unusable hash, token_version bumped.
"""
import secrets

import sqlalchemy as sa
from alembic import op

from app.core.security import get_password_hash

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns("users")}
    if "auth_provider" not in existing:
        op.add_column("users", sa.Column("auth_provider", sa.String(), nullable=False, server_default="password"))
    if "email_verified" not in existing:
        op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "token_version" not in existing:
        op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))

    users = sa.table(
        "users",
        sa.column("id", sa.Integer),
        sa.column("avatar_url", sa.String),
        sa.column("auth_provider", sa.String),
        sa.column("email_verified", sa.Boolean),
        sa.column("hashed_password", sa.String),
        sa.column("token_version", sa.Integer),
    )
    rows = bind.execute(
        sa.select(users.c.id).where(
            users.c.auth_provider == "password",
            users.c.avatar_url.isnot(None),
            users.c.avatar_url != "",
        )
    ).fetchall()
    for (user_id,) in rows:
        bind.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(
                auth_provider="google",
                email_verified=True,
                hashed_password=get_password_hash(secrets.token_urlsafe(48)),
                token_version=users.c.token_version + 1,
            )
        )


def downgrade() -> None:
    pass
