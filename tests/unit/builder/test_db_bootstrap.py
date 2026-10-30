from __future__ import annotations

import pytest

from builder import db as builder_db


class _FakeConn:
    def __init__(self, log):
        self.log = log

    async def execute(self, statement):
        self.log.append(str(statement))


class _FakeEngine:
    def __init__(self, url, log):
        self.url = url
        self.log = log
        self.disposed = False

    def begin(self):
        engine = self

        class _Ctx:
            async def __aenter__(self):
                return _FakeConn(engine.log)

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def dispose(self):
        self.disposed = True


async def test_ensure_mysql_database_connects_without_a_database_and_creates_it(monkeypatch):
    engines: list[_FakeEngine] = []
    statements: list[str] = []

    def fake_create(url, **kwargs):
        engine = _FakeEngine(url, statements)
        engines.append(engine)
        return engine

    monkeypatch.setattr(builder_db, "create_async_engine", fake_create)

    await builder_db.ensure_mysql_database("mysql+asyncmy://root:p%40ss%2Fw@mariadb:3306/mdjebot_builder")

    [engine] = engines
    # Regression: connecting WITH the database fails on a fresh server ("Unknown database").
    assert engine.url.database is None
    assert (engine.url.username, engine.url.password, engine.url.host, engine.url.port) == (
        "root",
        "p@ss/w",
        "mariadb",
        3306,
    )
    assert engine.url.drivername == "mysql+asyncmy"
    assert statements == [
        "CREATE DATABASE IF NOT EXISTS `mdjebot_builder` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    ]
    assert engine.disposed


async def test_ensure_mysql_database_ignores_sqlite_and_rejects_unsafe_names(monkeypatch):
    monkeypatch.setattr(builder_db, "create_async_engine", lambda *a, **k: pytest.fail("must not connect"))
    await builder_db.ensure_mysql_database("sqlite+aiosqlite:///:memory:")
    with pytest.raises(ValueError):
        await builder_db.ensure_mysql_database("mysql+asyncmy://root:x@h/evil`name")
