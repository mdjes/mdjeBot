"""Wires the builder together: database, Telethon client, scheduler and event handlers."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import TelegramClient, events

from builder.app import BuilderApp
from builder.ctx import TelethonCtx
from builder.db import Database, ensure_mysql_database
from builder.models import Plan
from builder.notify import TelethonNotifier
from builder.security import SecretBox
from builder.services.lifecycle import expire_stale_payments, run_expiry_pass
from builder.services.ops import TenantOps
from builder.services.provisioner import Provisioner
from builder.settings import Settings

log = logging.getLogger("builder")


async def seed_defaults(db: Database) -> None:
    """Give a fresh install one plan so the admin has something to edit."""
    from sqlalchemy import func, select

    async with db.session() as session:
        if not (await session.execute(select(func.count(Plan.id)))).scalar_one():
            session.add(Plan(name="ماهانه", price=150_000, duration_days=30))
            await session.commit()


async def run_builder(settings: Settings, stop_event: asyncio.Event | None = None) -> None:
    await ensure_mysql_database(settings.database_url)
    db = Database(settings.database_url)
    await db.create_all()
    await seed_defaults(db)

    Path(settings.session_path).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(settings.session_path, settings.api_id, settings.api_hash)
    client.parse_mode = "html"
    await client.start(bot_token=settings.bot_token)
    me = await client.get_me()
    log.info("Builder bot connected as @%s", me.username)

    box = SecretBox(settings.secret_key)
    notifier = TelethonNotifier(client)
    ops = TenantOps(db, settings, box, Provisioner(settings))
    app = BuilderApp(settings, db, ops, notifier, box)

    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def _on_message(event):
        await app.on_message(await TelethonCtx.from_event(event))

    @client.on(events.CallbackQuery())
    async def _on_callback(event):
        await app.on_callback(await TelethonCtx.from_event(event))

    scheduler = AsyncIOScheduler()

    async def _expiry_job():
        try:
            counts = await run_expiry_pass(db, ops, notifier)
            if any(counts.values()):
                log.info("expiry pass: %s", counts)
        except Exception:
            log.exception("expiry pass failed")

    async def _stale_payments_job():
        try:
            rejected = await expire_stale_payments(db)
            if rejected:
                log.info("rejected %d stale payments", rejected)
        except Exception:
            log.exception("stale payment cleanup failed")

    scheduler.add_job(_expiry_job, "interval", minutes=10, max_instances=1, coalesce=True)
    scheduler.add_job(_stale_payments_job, "interval", hours=1, max_instances=1, coalesce=True)
    scheduler.start()

    try:
        if stop_event is None:
            await client.run_until_disconnected()
        else:
            waiter = asyncio.create_task(stop_event.wait())
            runner = asyncio.create_task(client.run_until_disconnected())
            await asyncio.wait({waiter, runner}, return_when=asyncio.FIRST_COMPLETED)
            for task in (waiter, runner):
                task.cancel()
    finally:
        scheduler.shutdown(wait=False)
        await client.disconnect()
        await db.dispose()
