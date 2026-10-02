"""Run one of the original SQL migration files inside a revision."""

from __future__ import annotations

from alembic import op

from ops_copilot.db.migrate import SQL_DIR


def run_sql_file(name: str) -> None:
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    # Straight to the driver: with no parameters psycopg sends the text
    # as-is, so several statements, DO $$ blocks, '::' casts and '%' all
    # pass through untouched (SQLAlchemy's text() would read ':name' as
    # a bind parameter). Same connection, same transaction as Alembic.
    op.get_bind().connection.driver_connection.execute(sql)
