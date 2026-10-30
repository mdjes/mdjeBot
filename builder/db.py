"""Async engine/session handling for the builder database."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
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
    server_engine = create_async_engine(parsed.set(database=None))
    try:
        async with server_engine.begin() as conn:
            await conn.execute(
                text(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            )
    finally:
        await server_engine.dispose()


def _upgrade_schema(conn) -> None:
    """Bring a database created by an older builder version up to date (create_all never alters tables)."""
    inspector = inspect(conn)
    if "builder_tenants" not in inspector.get_table_names():
        return
    columns = {column["name"]: column for column in inspector.get_columns("builder_tenants")}
    if "pending_days" not in columns:
        conn.execute(text("ALTER TABLE builder_tenants ADD COLUMN pending_days INTEGER NOT NULL DEFAULT 0"))
    if conn.dialect.name == "mysql":
        # A tenant now exists before its bot token does (purchase first, token afterwards).
        if not columns["bot_id"]["nullable"]:
            conn.execute(text("ALTER TABLE builder_tenants MODIFY bot_id BIGINT NULL"))
        if not columns["token_enc"]["nullable"]:
            conn.execute(text("ALTER TABLE builder_tenants MODIFY token_enc TEXT NULL"))
