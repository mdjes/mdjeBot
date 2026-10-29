"""The builder bot controller: routing, conversation state and access checks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from builder.db import Database
from builder.flows.admin import AdminFlows
from builder.flows.user import UserFlows
from builder.models import utcnow
from builder.notify import Notifier
from builder.routing import collect
from builder.security import SecretBox
from builder.services import billing
from builder.services.cfg import Cfg, load_cfg
from builder.services.ops import TenantOps
from builder.services.telegram_api import BotInfo, fetch_bot_info
from builder.settings import Settings
from builder.ui import Btn, Buttons

log = logging.getLogger("builder.app")

STATE_TTL = timedelta(minutes=30)


@dataclass
class UserState:
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    updated: Any = field(default_factory=utcnow)


class BuilderApp(UserFlows, AdminFlows):
    def __init__(
        self,
        settings: Settings,
        db: Database,
        ops: TenantOps,
        notifier: Notifier,
        box: SecretBox,
        *,
        check_token: Callable[[str], Awaitable[BotInfo]] = fetch_bot_info,
    ):
        self.settings = settings
        self.db = db
        self.ops = ops
        self.notifier = notifier
        self.box = box
        self.check_token = check_token
        self.states: dict[int, UserState] = {}
        self._background: set[asyncio.Task] = set()
        self.routes, self.state_handlers = collect(self)

    # ── conversation state ────────────────────────────────────────────────────
    def set_state(self, user_id: int, name: str, **data: Any) -> UserState:
        state = UserState(name=name, data=dict(data))
        self.states[user_id] = state
        return state

    def get_state(self, user_id: int) -> UserState | None:
        state = self.states.get(user_id)
        if state is None:
            return None
        if utcnow() - state.updated > STATE_TTL:
            del self.states[user_id]
            return None
        state.updated = utcnow()
        return state

    def clear_state(self, user_id: int) -> None:
        self.states.pop(user_id, None)

    # ── helpers ───────────────────────────────────────────────────────────────
    def is_admin(self, user_id: int) -> bool:
        return user_id in self.settings.admin_ids

    async def cfg(self) -> Cfg:
        async with self.db.session() as session:
            return await load_cfg(session)

    async def show(self, ctx, text: str, buttons: Buttons | None = None) -> None:
        """Edit the message the button was on, or send a new one after a typed message."""
        if ctx.is_callback:
            await ctx.edit(text, buttons)
        else:
            await ctx.reply(text, buttons)

    async def _gate(self, ctx) -> bool:
        """Record the user and apply ban / maintenance rules. ``False`` means the update was handled."""
        async with self.db.session() as session:
            user = await billing.get_or_create_user(session, ctx.user_id, ctx.first_name, ctx.username)
            await session.commit()
            banned = user.banned
            cfg = await load_cfg(session)
        if self.is_admin(ctx.user_id):
            return True
        if banned:
            await ctx.answer("🚫 دسترسی شما مسدود شده است.", alert=True)
            if not ctx.is_callback:
                await ctx.reply("🚫 دسترسی شما مسدود شده است.")
            return False
        if cfg.bool("maintenance"):
            await ctx.answer("🛠 ربات در حال تعمیر است.", alert=True)
            if not ctx.is_callback:
                await ctx.reply("🛠 ربات‌ساز در حال بروزرسانی است؛ کمی بعد دوباره تلاش کنید.")
            return False
        return True

    # ── entry points ──────────────────────────────────────────────────────────
    async def on_message(self, ctx) -> None:
        if not await self._gate(ctx):
            return
        text = ctx.text.strip()
        try:
            if text.split(" ")[0] in ("/start", "/menu", "/cancel"):
                await self.home(ctx)
            elif text == "/admin" and self.is_admin(ctx.user_id):
                await self.admin_home(ctx)
            else:
                state = self.get_state(ctx.user_id)
                handler = self.state_handlers.get(state.name) if state else None
                if handler is None:
                    await self.home(ctx)
                else:
                    await handler(ctx, state)
        except Exception:
            log.exception("message handler failed for user %s", ctx.user_id)
            await ctx.reply("❌ خطای داخلی رخ داد. دوباره تلاش کنید.", [[Btn("🏠 منوی اصلی", "u:home")]])

    async def on_callback(self, ctx) -> None:
        if not await self._gate(ctx):
            return
        parts = ctx.data.split(":")
        key, args = ":".join(parts[:2]), parts[2:]
        handler = self.routes.get(key)
        if handler is None:
            await ctx.answer()
            return
        if key.startswith("a:") and not self.is_admin(ctx.user_id):
            await ctx.answer("⛔️ دسترسی ندارید.", alert=True)
            return
        try:
            await handler(ctx, *args)
        except Exception:
            log.exception("callback %s failed for user %s", ctx.data, ctx.user_id)
            await ctx.answer("❌ خطای داخلی رخ داد.", alert=True)
        await ctx.answer()
