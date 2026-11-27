"""Back up my bot now, and put it back to a backup (the button under every backup file the builder sends)."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from builder.models import Tenant, TenantStatus, utcnow
from builder.routing import route
from builder.services.backup import BACKED_UP, MAX_ARCHIVE_BYTES, restore_button
from builder.ui import Btn
from builder.util import esc

MANUAL_BACKUP_GAP = timedelta(minutes=5)  # a dump is heavy: one on request per bot every few minutes


class BackupFlows:
    def _can_back_up(self, tenant: Tenant) -> bool:
        return tenant.status in BACKED_UP and tenant.db_password_enc is not None

    async def _owner_tenant(self, ctx, tenant_id: str) -> Tenant | None:
        """The bot behind a backup button, only for its owner."""
        if not tenant_id.isdigit():
            return None
        async with self.db.session() as s:
            tenant = await s.get(Tenant, int(tenant_id))
        if tenant is None or tenant.owner_id != ctx.user_id or tenant.status == TenantStatus.DELETED:
            return None
        return tenant

    @route("u:bkp")
    async def backup_now(self, ctx, tenant_id: str = "") -> None:
        tenant = await self._owner_tenant(ctx, tenant_id)
        if tenant is None or not self._can_back_up(tenant):
            await ctx.answer("این ربات پشتیبانی برای گرفتن ندارد.", alert=True)
            return
        asked = self._backup_asked
        last = asked.get(tenant.id)
        if last is not None and utcnow() - last < MANUAL_BACKUP_GAP:
            await ctx.answer("📦 چند دقیقه پیش پشتیبان گرفته شد؛ کمی بعد دوباره تلاش کنید.", alert=True)
            return
        asked[tenant.id] = utcnow()
        await ctx.answer("⏳ در حال گرفتن پشتیبان…")
        result = await self.backups.backup_and_send(tenant.id, trigger="manual")
        if result.ok:
            await ctx.answer("✅ فایل پشتیبان برایتان ارسال شد.", alert=True)
        else:
            asked.pop(tenant.id, None)
            await ctx.answer(f"❌ پشتیبان‌گیری انجام نشد: {(result.error or '')[:150]}", alert=True)

    @route("u:rest")
    async def restore_ask(self, ctx, tenant_id: str = "") -> None:
        tenant = await self._owner_tenant(ctx, tenant_id)
        if tenant is None or not self._can_back_up(tenant):
            await ctx.answer("این ربات پیدا نشد.", alert=True)
            return
        await ctx.edit(
            f"⚠️ <b>بازیابی ربات {esc(self.tenant_label(tenant))}</b>\n\n"
            "تمام اطلاعات فعلی ربات (کاربران، سرویس‌ها، موجودی‌ها و تنظیمات) با محتوای همین فایل جایگزین می‌شود "
            "و ربات چند لحظه خاموش می‌ماند. اگر چیزی در بازیابی خراب شود، اطلاعات فعلی خودکار برمی‌گردد.\n\n"
            "ادامه می‌دهید؟",
            [[Btn("✅ بله، بازیابی کن", f"u:restok:{tenant.id}")], [Btn("↩️ انصراف", f"u:restno:{tenant.id}")]],
        )

    @route("u:restno")
    async def restore_cancel(self, ctx, tenant_id: str = "") -> None:
        tenant = await self._owner_tenant(ctx, tenant_id)
        if tenant is None:
            return
        await ctx.edit(
            f"🗄 فایل پشتیبان ربات {esc(self.tenant_label(tenant))}\nبرای برگرداندن ربات به این نسخه، دکمهٔ زیر را بزنید.",
            restore_button(tenant.id),
        )

    @route("u:restok")
    async def restore_run(self, ctx, tenant_id: str = "") -> None:
        tenant = await self._owner_tenant(ctx, tenant_id)
        if tenant is None or not self._can_back_up(tenant):
            await ctx.answer("این ربات پیدا نشد.", alert=True)
            return
        work_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, "mdjebot-restore-dl-"))
        try:
            archive = work_dir / "backup.zip"
            if not await ctx.download_attachment(archive, max_bytes=MAX_ARCHIVE_BYTES):
                await ctx.answer("فایل پشتیبان روی این پیام پیدا نشد.", alert=True)
                return
            await ctx.edit("⏳ در حال بازیابی… ربات چند لحظه خاموش می‌شود.")
            result = await self.backups.restore(tenant.id, archive)
        finally:
            await asyncio.to_thread(shutil.rmtree, work_dir, True)

        label = esc(self.tenant_label(tenant))
        if result.ok:
            text = f"✅ ربات {label} به همین نسخه برگشت و دوباره روشن شد."
            for admin_id in self.settings.admin_ids:
                await self.notifier.send(
                    admin_id, f"♻️ مالک <code>{ctx.user_id}</code> ربات #{tenant.id} را بازیابی کرد."
                )
        elif result.rolled_back:
            text = f"❌ بازیابی انجام نشد ({esc((result.error or '')[:200])}).\nاطلاعات قبلی ربات برگردانده شد."
        else:
            text = f"❌ بازیابی انجام نشد: {esc((result.error or '')[:300])}"
        await ctx.edit(text, restore_button(tenant.id))
