import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from tempfile import NamedTemporaryFile

from donor_bot.models import Event, Registration


async def export_event_stats(session: AsyncSession, file_path: str) -> str:
    """Выгружает статистику по всем ДД в один Excel."""
    events = (await session.execute(select(Event))).scalars().all()
    rows = []
    for event in events:
        regs = (await session.execute(select(Registration).where(Registration.event_id == event.id))).scalars().all()
        # Определяем статистику на основе фактических записей, а не счётчика `slots_taken`,
        # так как последний мог быть расхлопан из-за исторической ошибки, когда регистрация
        # не сохранялась корректно.  Теперь «Занято» отражает число записей со статусом
        # отличным от *cancelled*.
        occupied = sum(1 for r in regs if r.status != "cancelled")
        donated = sum(1 for r in regs if r.status == "donated")
        noshow = sum(1 for r in regs if r.status == "no-show")

        rows.append({
            "Дата": event.date,
            "Центр": event.blood_center,
            "Всего слотов": event.slots_total,
            "Занято": occupied,
            "Сдал": donated,
            "No-show": noshow,
        })
    pd.DataFrame(rows).to_excel(file_path, index=False)
    return file_path


async def make_report(session: AsyncSession):
    """Legacy wrapper used by admin_menu; returns (file_path, summary_text)."""
    from datetime import datetime
    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    await export_event_stats(session, tmp.name)

    # build simple text summary
    df = pd.read_excel(tmp.name)
    lines: list[str] = ["Сводка по донорским акциям:"]
    for _, row in df.iterrows():
        date_str = pd.to_datetime(row["Дата"]).strftime("%d.%m.%Y")
        lines.append(
            f"\n{date_str} – {row['Центр']}\n"
            f"  зарегистрировано: {row['Занято']} / {row['Всего слотов']}\n"
            f"  пришли: {row['Сдал']}\n"
            f"  no-show: {row['No-show']}"
        )
    summary = "\n".join(lines)
    return tmp.name, summary


# ----------------- NEW: single event export -----------------


async def export_single_event(session: AsyncSession, event_id: int, file_path: str) -> str:
    """Экспортирует подробную таблицу по конкретному мероприятию: список доноров и их статусы."""
    from donor_bot.models import Registration, Donor
    from sqlmodel import select

    event = await session.get(Event, event_id)
    if not event:
        raise ValueError("Мероприятие не найдено")

    regs = (
        await session.execute(select(Registration).where(Registration.event_id == event_id))
    ).scalars().all()

    rows = []
    for r in regs:
        donor = await session.get(Donor, r.donor_id)
        rows.append(
            {
                "ФИО": getattr(donor, "full_name", "-"),
                "Категория": getattr(donor, "category", "-"),
                "Телефон": getattr(donor, "phone", "-"),
                "Статус регистрации": r.status,
            }
        )

    if not rows:
        rows.append({"ФИО": "-", "Категория": "-", "Телефон": "-", "Статус регистрации": "нет данных"})

    df = pd.DataFrame(rows)
    df.to_excel(file_path, index=False)
    return file_path


# --------- Export donor table ---------


async def export_donors(session: AsyncSession, file_path: str) -> str:
    """Экспортирует всю таблицу доноров в Excel с расширенными полями."""
    from donor_bot.models import Donor
    donors = (await session.execute(select(Donor))).scalars().all()

    import pandas as pd
    rows = []
    for d in donors:
        rows.append(
            {
                "ФИО": d.full_name,
                "Группа": d.group,
                "Кол-во Гаврилова": d.gavrilova_count,
                "Кол-во ФМБА": d.fmba_count,
                "Сумма": d.total_sum,
                "Дата последней донации Гаврилова": d.last_gavrilova,
                "Дата последней донации ФМБА": d.last_fmba,
                "Контакты соцсети": d.social,
                "Телефон": d.phone,
            }
        )

    pd.DataFrame(rows).to_excel(file_path, index=False)
    return file_path
