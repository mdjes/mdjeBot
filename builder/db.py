"""Async engine/session handling for the builder database."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from builder.models import Base


class Database:
    def __init__(self, url: str):
        self.url = url
        kwargs: dict = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
        else:
            kwargs.update(pool_size=10, max_overflow=10, pool_recycle=300, pool_pre_ping=True)
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self._maker = async_sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        return self._maker()

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_upgrade_schema)

    async def dispose(self) -> None:
        await self.engine.dispose()


async def ensure_mysql_database(url: str) -> None:
    """Create the builder database on the MariaDB server if it does not exist yet."""
    parsed = make_url(url)
    if not parsed.drivername.startswith("mysql") or not parsed.database:
        return
    database = parsed.database
    if not database.replace("_", "").isalnum():
        raise ValueError(f"unsafe database name: {database!r}")
    # URL.set(database=None) keeps the old database, so build the server-level URL explicitly.
    server_url = URL.create(
        parsed.drivername,
        username=parsed.username,
        password=parsed.password,
        host=parsed.host,
        port=parsed.port,
        query=parsed.query,
    )
    server_engine = create_async_engine(server_url)
    try:
        async with server_engine.begin() as conn:
            await conn.execute(
                text(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            )
    finally:
        await server_engine.dispose()


def _literal(value) -> str | None:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _add_column_sql(dialect, table_name: str, column) -> str:
    """``ALTER TABLE ... ADD COLUMN`` for a column that older databases do not have yet."""
    parts = [f"ALTER TABLE {table_name} ADD COLUMN {column.name} {column.type.compile(dialect=dialect)}"]
    default = getattr(getattr(column, "default", None), "arg", None)
    literal = _literal(default) if not callable(default) else None
    if literal is not None:
        parts.append(f"NOT NULL DEFAULT {literal}" if not column.nullable else f"DEFAULT {literal}")
    else:
        parts.append("NULL")  # no scalar default to backfill existing rows with
    return " ".join(parts)


def _upgrade_schema(conn) -> None:
    """Bring a database created by an older builder version up to date (create_all never alters tables)."""
    inspector = inspect(conn)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        present = {column["name"]: column for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in present:
                conn.execute(text(_add_column_sql(conn.dialect, table.name, column)))

    if "builder_tenants" in existing and conn.dialect.name == "mysql":
        columns = {column["name"]: column for column in inspector.get_columns("builder_tenants")}
        # A tenant now exists before its bot token does (purchase first, token afterwards).
        if "bot_id" in columns and not columns["bot_id"]["nullable"]:
            conn.execute(text("ALTER TABLE builder_tenants MODIFY bot_id BIGINT NULL"))
        if "token_enc" in columns and not columns["token_enc"]["nullable"]:
            conn.execute(text("ALTER TABLE builder_tenants MODIFY token_enc TEXT NULL"))
