from __future__ import annotations

from sqlalchemy import inspect, text

from builder.db import Database


async def test_create_all_adds_columns_missing_from_an_older_tenants_table():
    db = Database("sqlite+aiosqlite:///:memory:")
    async with db.engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE builder_tenants (id INTEGER PRIMARY KEY, owner_id BIGINT NOT NULL, "
                "admin_ids VARCHAR(255) NOT NULL, status VARCHAR(16))"
            )
        )
        await conn.execute(
            text("INSERT INTO builder_tenants (id, owner_id, admin_ids, status) VALUES (1, 5, '5', 'running')")
        )

    await db.create_all()
    await db.create_all()  # idempotent

    async with db.engine.connect() as conn:
        columns = await conn.run_sync(lambda c: [col["name"] for col in inspect(c).get_columns("builder_tenants")])
        pending = (await conn.execute(text("SELECT pending_days FROM builder_tenants WHERE id = 1"))).scalar_one()
    assert "pending_days" in columns and pending == 0
    await db.dispose()
