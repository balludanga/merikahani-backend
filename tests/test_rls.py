"""Row Level Security on the public tables (Supabase publishes them over its own API).

There is no Postgres on the development machine, so the Postgres path is checked against a stand-in
connection that records the SQL it is given; the one-line catalog query it relies on is the part to
look at the first time this meets a real Supabase (see AGENTS.md).
"""

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql

from app.core.rls import enable_row_level_security


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Savepoint:
    def __enter__(self):
        return self

    def __exit__(self, kind, value, trace):
        return False


class FakePostgres:
    """Answers the catalog query with the given (name, mine) rows and records every ALTER."""

    def __init__(self, tables, refuse=()):
        self.dialect = postgresql.dialect()
        self._tables, self._refuse = tables, set(refuse)
        self.altered, self.committed, self.rolled_back = [], 0, 0

    def execute(self, statement):
        sql = str(statement)
        if "pg_class" in sql:
            return _Rows(self._tables)
        table = sql.split("public.")[1].split(" ")[0].strip('"')
        if table in self._refuse:
            raise RuntimeError("permission denied")
        self.altered.append(sql)
        return _Rows([])

    def begin_nested(self):
        return _Savepoint()

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def test_sqlite_is_left_alone():
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        connection.execute(text("CREATE TABLE t (id integer)"))
        assert enable_row_level_security(connection) == []


def test_every_unprotected_table_it_owns_is_switched_on():
    conn = FakePostgres([("posts", True), ("users", True), ("alembic_version", True)])
    assert enable_row_level_security(conn) == ["posts", "users", "alembic_version"]
    assert conn.altered == [
        "ALTER TABLE public.posts ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE public.users ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE public.alembic_version ENABLE ROW LEVEL SECURITY",
    ]
    assert conn.committed == 1


def test_nothing_is_forced_on_the_owner():
    """FORCE would apply RLS to the table's owner, which is the role the app connects as."""
    conn = FakePostgres([("posts", True)])
    enable_row_level_security(conn)
    assert all("FORCE" not in sql for sql in conn.altered)


def test_a_table_the_role_does_not_own_is_reported_not_attempted(caplog):
    conn = FakePostgres([("posts", True), ("theirs", False)])
    with caplog.at_level(logging.WARNING):
        assert enable_row_level_security(conn) == ["posts"]
    assert not any("theirs" in sql for sql in conn.altered)
    assert "theirs" in caplog.text


def test_one_table_that_refuses_does_not_stop_the_rest(caplog):
    conn = FakePostgres([("a", True), ("b", True), ("c", True)], refuse=["b"])
    with caplog.at_level(logging.ERROR):
        assert enable_row_level_security(conn) == ["a", "c"]
    assert "public.b" in caplog.text


def test_a_strange_table_name_is_quoted():
    quote = postgresql.dialect().identifier_preparer.quote
    assert quote('Odd "name"') == '"Odd ""name"""'
    assert quote("Posts") == '"Posts"'


def test_a_failing_catalog_query_never_stops_the_app(caplog):
    class Broken(FakePostgres):
        def execute(self, statement):
            raise RuntimeError("no catalog")

    conn = Broken([])
    with caplog.at_level(logging.ERROR):
        assert enable_row_level_security(conn) == []
    assert conn.rolled_back == 1
