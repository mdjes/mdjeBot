"""Every Friday evening (Tehran) send each customer a short message about the week's usage.

Switched off by default (``weekly_usage_report_mode``). A customer can turn it off for themselves with the button
under the message. The week counts from Saturday to Friday, and a week is sent at most once: the week is marked when
the run finishes, and a per-week Redis set of the customers already sent to keeps a run that was cut short (a
restart) from messaging the same people twice.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytz
from pasarguard import PasarguardAPI
from telethon import Button, errors

from app import Mdje
from app.db.crud.panels import PanelsManager
from app.db.crud.plans import PlanManager
from app.db.crud.services import ServiceCRUD
from app.db.crud.settings import SettingsManager
from app.db.crud.user import set_user_status
from app.db.redis import get_redis
from app.logger import LogTag, get_logger
from app.services.billing.renewal import require_panel_userid
from app.services.usage_report import (
    CALLBACK_OPT_OUT,
    CALLBACK_RENEW,
    ServiceWeek,
    needs_more_volume,
    render_user_report,
    renew_target,
    suggest_plan,
    summarize_week,
)
from app.telegram.shared.utils.usage_chart import fetch_daily_usage
from app.telegram.state.keys import build_weekly_report_key

logger = get_logger(__name__)

IRAN_TZ = pytz.timezone("Asia/Tehran")
SEND_HOUR = 18  # Friday, 18:00 Tehran
FRIDAY = 4
SATURDAY = 5
_BATCH_SIZE = 500
_PARALLEL_PANEL_CALLS = 4
_SEND_PAUSE = 0.06  # stay well under Telegram's ~30 messages a second
_SKIPPED_STATUSES = {"disabled", "expired", "on_hold"}


def week_key(now: datetime) -> str:
    """The Saturday-to-Friday week ``now`` falls in, named by its Saturday (Tehran date)."""
    local = now.astimezone(IRAN_TZ)
    saturday = local.date() - timedelta(days=(local.weekday() - SATURDAY) % 7)
    return saturday.isoformat()


def is_due(now: datetime, last_sent_week: str) -> bool:
    """Friday from 18:00 on, and the week is not sent yet."""
    local = now.astimezone(IRAN_TZ)
    return local.weekday() == FRIDAY and local.hour >= SEND_HOUR and week_key(now) != (last_sent_week or "")


async def is_opted_out(user_id: int) -> bool:
    redis = await get_redis()
    if redis is None:
        return False
    try:
        return bool(await redis.sismember(build_weekly_report_key("optout"), str(user_id)))
    except Exception as exc:
        logger.warning("%s weekly report opt-out lookup failed: %s", LogTag.JOB, exc)
        return False


async def opt_out(user_id: int) -> bool:
    redis = await get_redis()
    if redis is None:
        return False
    try:
        await redis.sadd(build_weekly_report_key("optout"), str(user_id))
    except Exception as exc:
        logger.warning("%s weekly report opt-out failed: %s", LogTag.JOB, exc)
        return False
    return True


async def _week_of_service(api, panel, service, today) -> ServiceWeek | None:
    """The numbers for one service, or None when it should not be reported (test, off, unreachable)."""
    try:
        panel_userid = require_panel_userid(service)
        user = await api.get_user_by_id(user_id=panel_userid, token=panel.cookie)
        status = str(getattr(getattr(user, "status", None), "value", getattr(user, "status", "")) or "")
        if status in _SKIPPED_STATUSES:
            return None
        daily = await fetch_daily_usage(panel, panel_userid, days=8)
    except Exception as exc:
        logger.warning("%s weekly usage: service %s skipped: %s", LogTag.JOB, service.code, exc)
        return None
    days_left = None
    if service.expiration_time:
        days_left = max(0, int((service.expiration_time - datetime.now().timestamp()) // 86400))
    return ServiceWeek(
        code=service.code,
        username=service.username,
        panel_code=service.in_panel,
        usage=summarize_week(daily, today),
        limit=int(getattr(user, "data_limit", 0) or 0),
        used=int(getattr(user, "used_traffic", 0) or 0),
        days_left=days_left,
    )


async def collect_weeks(today) -> dict[int, list[ServiceWeek]]:
    """Every customer's services with a week of usage, by Telegram user id (paid services only)."""
    service_crud = ServiceCRUD()
    gate = asyncio.Semaphore(_PARALLEL_PANEL_CALLS)
    by_user: dict[int, list[ServiceWeek]] = {}

    async def one(api, panel, service):
        async with gate:
            return service, await _week_of_service(api, panel, service, today)

    for panel in await PanelsManager().get_all_panels():
        api = PasarguardAPI(panel.base_url)
        after_code = None
        while True:
            batch = await service_crud.get_all_services_by_panels_batch(
                [panel.code], limit=_BATCH_SIZE, after_code=after_code
            )
            if not batch:
                break
            after_code = batch[-1].code
            wanted = [s for s in batch if s.id and s.panel_userid and getattr(s, "is_test", False) is not True]
            for service, week in await asyncio.gather(*(one(api, panel, s) for s in wanted)):
                if week is not None:
                    by_user.setdefault(int(service.id), []).append(week)
            if len(batch) < _BATCH_SIZE:
                break
    return by_user


async def _send(user_id: int, text: str, buttons) -> bool:
    for attempt in (1, 2):
        try:
            await Mdje.send_message(user_id, text, buttons=buttons, parse_mode="html", link_preview=False)
            return True
        except errors.FloodWaitError as exc:
            if attempt == 2 or exc.seconds > 60:
                return False
            await asyncio.sleep(exc.seconds + 1)
        except errors.InputUserDeactivatedError:
            await set_user_status(user_id, "DeleteAccount")
            return False
        except errors.UserIsBlockedError:
            await set_user_status(user_id, "BlockedBot")
            return False
        except Exception as exc:
            logger.warning("%s weekly usage: send to %s failed: %s", LogTag.JOB, user_id, exc)
            return False
    return False


def _buttons(weeks: list[ServiceWeek]):
    rows = []
    target = renew_target(weeks)
    if target is not None:
        rows.append([Button.inline("💎 تمدید / ارتقای سرویس", CALLBACK_RENEW.format(code=target.code).encode())])
    rows.append([Button.inline("🔕 این گزارش را نفرست", CALLBACK_OPT_OUT.encode())])
    return rows


async def send_weekly_usage_reports(*, now: datetime | None = None, force: bool = False) -> dict[str, int]:
    """Send the weekly report when it is due (``force`` sends it now, for a test run). Returns what was done."""
    now = now or datetime.now(IRAN_TZ)
    counts = {"sent": 0, "skipped": 0, "failed": 0}
    settings = await SettingsManager().get_settings()
    if settings is None or not settings.weekly_usage_report_mode:
        return counts
    last_week = str(settings.weekly_usage_report_last_week or "")
    if not force and not is_due(now, last_week):
        return counts

    week = week_key(now)
    redis = await get_redis()
    sent_key = build_weekly_report_key(f"sent:{week}")
    weeks_by_user = await collect_weeks(now.astimezone(IRAN_TZ).date())
    plans = await PlanManager().get_all_plans()

    for user_id, weeks in weeks_by_user.items():
        weeks = [w for w in weeks if w.usage.total > 0]
        if not weeks or await is_opted_out(user_id):
            counts["skipped"] += 1
            continue
        if redis is not None and not force:
            try:
                if await redis.sismember(sent_key, str(user_id)):
                    counts["skipped"] += 1
                    continue
            except Exception:
                pass
        for w in weeks:
            if needs_more_volume(w):
                w.plan = suggest_plan(plans, w)
        if await _send(user_id, render_user_report(weeks), _buttons(weeks)):
            counts["sent"] += 1
            if redis is not None:
                try:
                    await redis.sadd(sent_key, str(user_id))
                    await redis.expire(sent_key, 10 * 24 * 3600)
                except Exception:
                    pass
        else:
            counts["failed"] += 1
        await asyncio.sleep(_SEND_PAUSE)

    if not force:
        await SettingsManager().update_setting(settings.id, weekly_usage_report_last_week=week)
    logger.info("%s weekly usage report %s: %s", LogTag.JOB, week, counts)
    return counts
