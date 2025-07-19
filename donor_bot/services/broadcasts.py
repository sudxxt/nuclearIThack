from typing import List

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from donor_bot.models import Donor


async def send_broadcast(bot: Bot, session: AsyncSession, tag: str, text: str) -> tuple[int, int]:
    """Send a broadcast to a subset of donors.

    Supported *tag* values:

    - ``all`` – everyone in the database.
    - ``student`` / ``staff`` / ``external`` – by donor.category.
    - ``dkm`` – donors who отметили флаг ``dkm_member``.
    - ``upcoming_registered`` – donors, зарегистрированные на ближайшее предстоящее мероприятие.
    - ``upcoming_not_registered`` – доноры, которые есть в базе, но *не* зарегистрированы на ближайшее мероприятие.
    - ``noshow_last`` – те, кто записался, но не пришёл (статус ``no-show``) на последнем прошедшем мероприятии.

    Unknown tags fallback to empty list to avoid accidental spam.
    """

    tag = tag.lower().strip()

    donors: List[Donor] = []

    # ---------------- Plain category tags ----------------
    if tag in {"all", "student", "staff", "external"}:
        query = select(Donor)
        if tag != "all":
            query = query.where(Donor.category == tag)
        donors = list((await session.execute(query)).scalars().all())

    # ---------------- DKM members ----------------
    elif tag == "dkm":
        donors = list((await session.execute(select(Donor).where(Donor.dkm_member.is_(True)))  # type: ignore[attr-defined]
                       ).scalars().all())

    # ---------------- Upcoming event helpers ----------------
    elif tag in {"upcoming_registered", "upcoming_not_registered"}:
        from datetime import date as _date
        from donor_bot.models import Event, Registration  # local import to avoid cycles
        # Выбираем ближайшее будущее мероприятие
        upcoming_event = (
            await session.execute(
                select(Event).where(Event.date >= _date.today()).order_by(Event.date)  # type: ignore[arg-type]
            )
        ).scalars().first()

        if not upcoming_event:  # нет будущих акций -> аудитория пуста
            donors = []
        else:
            # find donor_ids based on registration status
            regs = (
                await session.execute(select(Registration).where(Registration.event_id == upcoming_event.id))
            ).scalars().all()

            donor_ids_registered = {r.donor_id for r in regs if r.status == "registered"}

            if tag == "upcoming_registered":
                donors = list((await session.execute(select(Donor).where(Donor.id.in_(donor_ids_registered)))  # type: ignore[attr-defined]
                                ).scalars().all())
            else:  # upcoming_not_registered
                donors = list(
                    (
                        await session.execute(
                            select(Donor).where(~Donor.id.in_(donor_ids_registered))  # type: ignore[attr-defined]  # pragma: allowlist ok
                        )
                    ).scalars().all()
                )

    # ---------------- No-show on last event ----------------
    elif tag == "noshow_last":
        from datetime import date as _date
        from donor_bot.models import Event, Registration

        last_event = (
            await session.execute(
                select(Event).where(Event.date < _date.today()).order_by(Event.date.desc())  # type: ignore[arg-type]
            )
        ).scalars().first()

        if last_event:
            regs = (
                await session.execute(
                    select(Registration).where(Registration.event_id == last_event.id, Registration.status == "no-show")
                )
            ).scalars().all()
            donor_ids = {r.donor_id for r in regs}
            donors = list(
                (await session.execute(select(Donor).where(Donor.id.in_(donor_ids)))  # type: ignore[attr-defined]
                 ).scalars().all()
             )

    # ---------------- Unknown tag ----------------
    else:
        donors = []

    success_count = 0
    fail_count = 0
    for donor in donors:
        try:
            formatted = f"📢 Рассылка:\n{text}"
            await bot.send_message(donor.tg_id, formatted)
            success_count += 1
        except TelegramAPIError:
            fail_count += 1

    return success_count, fail_count
