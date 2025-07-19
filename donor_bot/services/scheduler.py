import shutil
from datetime import datetime, date
from datetime import timedelta, datetime as _dt

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select

from donor_bot.config import settings
from donor_bot.db import SessionLocal
from donor_bot.models import Registration
from donor_bot.models import Event


async def send_noshow_polls(bot: Bot):
    async with SessionLocal() as session:
        today = date.today()
        registrations = (await session.execute(
            select(Registration)
            .join(Event)
            .where(Event.date == today, Registration.status == "registered")
        )).scalars().all()

        for reg in registrations:
            try:
                await bot.send_poll(
                    reg.donor_id,
                    "Почему не пришли на донацию?",
                    ["медотвод", "личные причины", "передумал(а)"],
                    is_anonymous=False
                )
                reg.status = "no-show"
                session.add(reg)
            except Exception:
                # Log error in a real app
                pass
        await session.commit()


async def daily_backup():
    db_path = settings.DB_PATH
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"{db_path.name}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
    shutil.copy(db_path, backup_path)


def schedule_jobs(bot: Bot):
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(send_noshow_polls, "cron", hour=0, minute=5, args=[bot])
    # Напоминание за день до мероприятия в 18:00
    scheduler.add_job(send_event_reminders, "cron", hour=18, minute=0, args=[bot])
    scheduler.add_job(daily_backup, "cron", hour=3, minute=0)

    # Напоминание за 2 часа до мероприятия (проверяем каждые 10 минут)
    scheduler.add_job(send_event_reminders_two_hours, "interval", minutes=10, args=[bot])
    scheduler.start()


async def send_event_reminders(bot: Bot):
    """За день до мероприятия напоминаем всем зарегистрированным."""
    from datetime import date, timedelta
    async with SessionLocal() as session:
        tomorrow = date.today() + timedelta(days=1)
        regs = (await session.execute(
            select(Registration).join(Event).where(Event.date == tomorrow, Registration.status == "registered")
        )).scalars().all()

        for reg in regs:
            try:
                event = await session.get(Event, reg.event_id)
                if not event:
                    continue
                await bot.send_message(
                    reg.donor_id,
                    f"📅 Напоминание: завтра ({event.date.strftime('%d.%m')}) День донора в {event.blood_center}. Приходите вовремя!"
                )
            except Exception:
                pass


# ----------- two‐hour reminders -----------


async def send_event_reminders_two_hours(bot: Bot):
    """Send reminders 2 hours before event start time.

    Runs every 10 minutes; selects events whose `start_time` is set and are
    starting between 1h55m and 2h05m from *now* (to avoid duplicates).
    """
    from zoneinfo import ZoneInfo

    now = _dt.now(tz=ZoneInfo("Europe/Moscow"))
    window_start = now + timedelta(hours=2) - timedelta(minutes=5)
    window_end = now + timedelta(hours=2) + timedelta(minutes=5)

    async with SessionLocal() as session:
        evs = (
            await session.execute(
                select(Event).where(Event.start_time.is_not(None)).where(Event.date >= date.today())  # type: ignore[attr-defined]
            )
        ).scalars().all()

        for ev in evs:
            if ev.start_time is None:
                continue
            ev_dt = _dt.combine(ev.date, ev.start_time, tzinfo=ZoneInfo("Europe/Moscow"))
            if window_start <= ev_dt <= window_end:
                # fetch registered donors
                regs = (
                    await session.execute(
                        select(Registration).where(Registration.event_id == ev.id, Registration.status == "registered")
                    )
                ).scalars().all()
                for reg in regs:
                    try:
                        await bot.send_message(
                            reg.donor_id,
                            f"⏰ Напоминание: через 2 часа День донора в {ev.blood_center}! Не опаздывайте."
                        )
                    except Exception:
                        # ignore errors (e.g., user blocked bot)
                        pass
