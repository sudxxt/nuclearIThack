from datetime import date, time
from donor_bot.utils.time import today_msk
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy import asc  # noqa: F401

from donor_bot.models import Event, Registration
from donor_bot.utils.center import normalize_center_name, CANONICAL_CENTERS


async def create_event(
    session: AsyncSession,
    event_date: date,
    blood_center: str,
    slots: int = 100,
    external_link: str | None = None,
    start_time: time | None = None,
    end_time: time | None = None,
) -> Event:
    from datetime import date as _d
    if event_date < today_msk():
        raise ValueError("Нельзя создавать мероприятие в прошлом.")

    # --- Validate & normalize blood center ---
    canonical_center = normalize_center_name(blood_center)
    if canonical_center is None:
        allowed_list = ", ".join(sorted(CANONICAL_CENTERS))
        raise ValueError(
            f"Доступны только следующие центры: {allowed_list}."
        )

    new_event = Event(
        date=event_date,
        blood_center=canonical_center,
        slots_total=slots,
        external_link=external_link,
        start_time=start_time,
        end_time=end_time,
    )
    session.add(new_event)
    await session.commit()
    await session.refresh(new_event)
    return new_event


async def get_event_by_date(session: AsyncSession, event_date: date) -> Optional[Event]:
    result = await session.execute(select(Event).where(Event.date == event_date))
    return result.scalars().first()


async def register_donor_for_event(session: AsyncSession, donor_id: int, event_id: int) -> tuple[bool, str]:
    """Registers a donor (identified by their Telegram *ID*) for an event.

    Historically the function expected *donor_id* to be the primary-key ID from the
    donors table, however throughout the codebase we always pass the Telegram ID
    (``Donor.tg_id``).  This mismatch caused registrations to be linked to a
    non-existent donor record and therefore broke statistics / exports.

    We now resolve the Telegram ID to the real ``Donor`` row before proceeding.
    """

    # Resolve donor by Telegram ID (which is what the callers pass in)
    from donor_bot.models import Donor  # local import to avoid circular deps

    donor_obj = (
        await session.execute(select(Donor).where(Donor.tg_id == donor_id))
    ).scalars().first()

    if not donor_obj or donor_obj.id is None:
        return False, "Перед записью необходимо зарегистрироваться в боте."

    real_donor_id = donor_obj.id

    # Re-check if a registration already exists with the *real* donor PK
    existing_reg_result = await session.execute(
        select(Registration).where(Registration.donor_id == real_donor_id, Registration.event_id == event_id)
    )
    if existing_reg_result.scalars().first():
        return False, "Вы уже зарегистрированы на это мероприятие."

    event = await session.get(Event, event_id)
    if not event:
        return False, "Мероприятие не найдено."

    if event.slots_taken >= event.slots_total:
        return False, "К сожалению, все места на это мероприятие уже заняты."

    event.slots_taken += 1
    new_registration = Registration(donor_id=real_donor_id, event_id=event_id)
    session.add(new_registration)
    session.add(event)
    await session.commit()

    return True, f"Вы успешно записаны на {event.date.strftime('%d.%m.%Y')}! ✅"


async def cancel_registration(session: AsyncSession, donor_id: int, event_id: int) -> bool:
    registration_result = await session.execute(
        select(Registration).where(Registration.donor_id == donor_id, Registration.event_id == event_id)
    )
    registration = registration_result.scalars().first()

    if registration and registration.status == "registered":
        registration.status = "cancelled"
        event = await session.get(Event, event_id)
        if event and event.slots_taken > 0:
            event.slots_taken -= 1
            session.add(event)
        session.add(registration)
        await session.commit()
        return True
    return False


async def get_upcoming_events(session: AsyncSession) -> List[Event]:
    result = await session.execute(
        select(Event)
        .where(Event.date >= today_msk())  # type: ignore[arg-type]
        .order_by(asc(Event.date))  # type: ignore[arg-type]
        )
    return list(result.scalars().all())


# ---------------- Импорт результатов мероприятия ----------------


async def import_event_results(session: AsyncSession, event_id: int, file_path: str) -> str:
    """Импортируется файл с результатами ДД.

    Ожидаемые столбцы (рус/англ распознаются):
        ФИО | full_name
        Телефон | phone
        Статус | status  (donated / no-show)
        ДКМ | dkm  (да/yes/1)

    Возвращает текст-отчёт.
    """
    import pandas as pd
    from donor_bot.models import Donor, Registration

    df = pd.read_excel(file_path)

    # normalize columns
    col_map = {
        "ФИО": "full_name",
        "Телефон": "phone",
        "Статус": "status",
        "ДКМ": "dkm",
    }
    df = df.rename(columns={ru: en for ru, en in col_map.items() if ru in df.columns})

    processed = 0
    created_regs = 0
    updated_regs = 0
    donors_not_found = 0

    # Подгружаем событие один раз – понадобится для обновления очков / серии
    event = await session.get(Event, event_id)
    if not event:
        raise ValueError("Мероприятие не найдено")

    for _, row in df.iterrows():
        processed += 1
        phone = row.get("phone")
        full_name = row.get("full_name")
        status = str(row.get("status", "")).strip().lower()
        dkm_flag = str(row.get("dkm", "")).strip().lower() in {"1", "yes", "да", "true"}

        donor = None
        if phone is not None and not pd.isna(phone):  # type: ignore[arg-type]
            donor = (
                await session.execute(select(Donor).where(Donor.phone == phone))
            ).scalars().first()
        if donor is None and full_name and not pd.isna(full_name):  # type: ignore[arg-type]
            donor = (
                await session.execute(select(Donor).where(Donor.full_name == full_name))
            ).scalars().first()

        if donor is None:
            donors_not_found += 1
            continue

        if dkm_flag:
            donor.dkm_member = True
            session.add(donor)

        if donor.id is None:
            donors_not_found += 1
            continue

        reg = (
            await session.execute(
                select(Registration)
                .where(Registration.donor_id == donor.id)  # type: ignore[arg-type]
                .where(Registration.event_id == event_id)  # type: ignore[arg-type]
            )
        ).scalars().first()

        is_new_donation = False
        if reg:
            prev_status = reg.status
            reg.status = status if status in {"donated", "no-show"} else reg.status
            # Засчитываем очки, только если статус стал «donated» впервые
            if prev_status != "donated" and reg.status == "donated":
                is_new_donation = True
            updated_regs += 1
        else:
            reg = Registration(donor_id=donor.id, event_id=event_id, status=status or "donated")
            session.add(reg)
            created_regs += 1
            if reg.status == "donated":
                is_new_donation = True

        # ------- Gamification: начисляем очки, обновляем серию -------
        if is_new_donation and status == "donated":
            from donor_bot.services.donors import apply_successful_donation
            await apply_successful_donation(session, donor, event.date, event.blood_center)

    await session.commit()

    return (
        f"Обработано строк: {processed}\n"
        f"Новых регистраций: {created_regs}\n"
        f"Обновлено регистраций: {updated_regs}\n"
        f"Доноров не найдено: {donors_not_found}"
    )
