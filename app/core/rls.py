"""Row Level Security on every table of the production database.

Why: the database is a Supabase Postgres. Supabase publishes everything in the `public` schema through
its own HTTP data API (PostgREST), and anyone who holds the project's public "anon" key can ask that API
for rows. A table with Row Level Security (RLS) switched off answers such a request in full: emails,
stories, payment records, sign-in state. Supabase's Security Advisor reports this as "RLS Disabled in
Public".

This app never uses that API. It talks to Postgres directly, as the role that owns the tables, and a
table's owner is exempt from RLS. So switching RLS on with no policies at all is the whole fix: the API
(anon and signed-in "authenticated" roles) then sees nothing, and the app notices no difference.

`enable_row_level_security()` runs after the migrations at every start, so a table added by a later
migration is covered the same day, without anyone having to remember. It changes only tables the
connected role owns (anything else would fail, and an app that cannot start is worse than a warning),
never forces RLS onto the owner (that would lock the app out), and does nothing on SQLite.
"""

import logging
from typing import List

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Ordinary and partitioned tables of the public schema that still have RLS off, and whether the connected
# role may alter them (it owns them, or belongs to the role that does).
_UNPROTECTED = """
SELECT c.relname AS name, pg_has_role(current_user, c.relowner, 'USAGE') AS mine
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p')
  AND NOT c.relrowsecurity
ORDER BY c.relname
"""


def _rollback(connection) -> None:
    try:
        connection.rollback()
    except Exception:  # noqa: BLE001
        pass


def enable_row_level_security(connection) -> List[str]:
    """Switch RLS on for every public table that lacks it; returns the names it changed.

    Never raises: a failure is logged and the app still starts, because this is a safety net and the
    schema is already in place by the time it runs.
    """
    if connection.dialect.name != "postgresql":
        return []
    done: List[str] = []
    try:
        quote = connection.dialect.identifier_preparer.quote
        rows = connection.execute(text(_UNPROTECTED)).fetchall()
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("Could not list the public tables to enable Row Level Security")
        _rollback(connection)
        return []
    for name, mine in rows:
        if not mine:
            logger.warning(
                "Row Level Security is off on public.%s but this database role does not own it; "
                "enable it by hand in the Supabase SQL editor.",
                name,
            )
            continue
        try:
            # a savepoint per table: one table that refuses does not undo the others
            with connection.begin_nested():
                connection.execute(text(f"ALTER TABLE public.{quote(name)} ENABLE ROW LEVEL SECURITY"))
            done.append(name)
        except Exception:  # noqa: BLE001
            logger.exception("Could not enable Row Level Security on public.%s", name)
    try:
        connection.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not save the Row Level Security change")
        _rollback(connection)
        return []
    if done:
        logger.info("Row Level Security switched on for %d table(s): %s", len(done), ", ".join(done))
    return done
