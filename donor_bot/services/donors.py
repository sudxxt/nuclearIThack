import shlex
from typing import Optional
from datetime import date

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy import asc

from donor_bot.models import Donor
from donor_bot.models import Registration
from donor_bot.models import Event


async def get_donor_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[Donor]:
    result = await session.execute(select(Donor).where(Donor.tg_id == tg_id))
    return result.scalars().first()


async def create_donor(
    session: AsyncSession,
    tg_id: int,
    phone: str,
    full_name: str,
    category: str,
    group: Optional[str] = None,
    pd_agreed: bool = True,
    lang: str = "ru",
) -> Donor:
    """Insert a new donor record.

    `lang` — UI language, defaults to Russian.
    """

    new_donor = Donor(
        tg_id=tg_id,
        phone=phone,
        full_name=full_name,
        category=category,
        group=group,
        pd_agreed=pd_agreed,
        lang=lang,
    )
    session.add(new_donor)
    await session.commit()
    await session.refresh(new_donor)
    return new_donor


async def update_donor_from_command(session: AsyncSession, user_input: str) -> str:
    parts = shlex.split(user_input)
    if len(parts) < 2:
        return "Неверный формат. Используйте: /donor edit <id> full_name='Новое ФИО' category=staff"

    try:
        donor_id = int(parts[0])
    except ValueError:
        return f"Неверный ID донора: {parts[0]}"

    donor = await session.get(Donor, donor_id)
    if not donor:
        return f"Донор с ID {donor_id} не найден."

    updates = {}
    for part in parts[1:]:
        if "=" not in part:
            return f"Неверный формат аргумента: {part}. Используйте 'ключ=значение'."
        key, value = part.split("=", 1)
        if not hasattr(donor, key):
            return f"Неверное поле: {key}"
        updates[key] = value

    for key, value in updates.items():
        setattr(donor, key, value)

    session.add(donor)
    await session.commit()
    return f"Данные донора {donor_id} обновлены."


# --------- add / delete helpers ---------


async def add_donor_from_command(session: AsyncSession, user_input: str) -> str:
    """Добавляет нового донора из текстовой команды.

    Пример использования:
        /donor add phone=+71234567890 full_name='Иванов Иван' category=student [tg_id=123] [group=ИКБО-01-22]
    """

    parts = shlex.split(user_input)
    if not parts:
        return "Неверный формат. Используйте ключ=значение."

    kwargs: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            return f"Неверный аргумент: {part}. Используйте ключ=значение."
        k, v = part.split("=", 1)
        kwargs[k] = v

    required = {"phone", "full_name", "category"}
    missing = required - kwargs.keys()
    if missing:
        return "Отсутствуют обязательные поля: " + ", ".join(sorted(missing))

    try:
        tg_id = int(kwargs.get("tg_id", "0"))
    except ValueError:
        return "tg_id должен быть числом"

    donor = Donor(
        tg_id=tg_id,
        phone=kwargs["phone"],
        full_name=kwargs["full_name"],
        category=kwargs["category"],
        group=kwargs.get("group"),
        social=kwargs.get("social"),
        lang=kwargs.get("lang", "ru"),
        pd_agreed=True,
    )
    session.add(donor)
    await session.commit()
    await session.refresh(donor)
    return f"Донор {donor.full_name} добавлен (ID={donor.id})."


async def delete_donor_by_id(session: AsyncSession, donor_id_str: str) -> str:
    """Удаляет донора по ID или TG ID."""

    try:
        donor_id = int(donor_id_str)
    except ValueError:
        return "ID должен быть числом"

    donor = await session.get(Donor, donor_id)
    if not donor:
        return f"Донор с ID {donor_id} не найден."

    await session.delete(donor)
    await session.commit()
    return f"Донор {donor_id} удалён."


# --------- history helpers ---------


async def get_donor_history(session: AsyncSession, tg_id: int):
    """Возвращает список (Event, Registration) для донора."""
    from donor_bot.models import Event
    result = await session.execute(
        select(Registration, Event)
        .join(Event, Registration.event_id == Event.id)  # type: ignore[arg-type]
        .join(Donor)  # type: ignore[arg-type]
        .where(Donor.tg_id == tg_id)
        .order_by(asc(Event.date))  # type: ignore[arg-type]
    )
    return result.all()


async def get_donor_by_credentials(session: AsyncSession, tg_id: Optional[int] = None, phone: Optional[str] = None) -> Optional[Donor]:
    if tg_id:
        return await get_donor_by_tg_id(session, tg_id)
    if phone:
        result = await session.execute(select(Donor).where(Donor.phone == phone))
        return result.scalars().first()
    return None


async def get_donor_registrations(session: AsyncSession, tg_id: int):
    result = await session.execute(
        select(Registration)
        .join(Donor)
        .where(Donor.tg_id == tg_id)
        .join(Event)
        .order_by(asc(Event.date))  # type: ignore[arg-type]т т й
    )
    return result.scalars().all()


async def import_donors_from_xlsx(session: AsyncSession, file_path: str) -> str:
    """Импорт доноров из Excel по новому шаблону.

    Поддерживает как «старые» имена столбцов (англ. атрибуты модели), так и русские названия
    из шаблона:

        ФИО | Группа | Кол-во Гаврилова | Кол-во ФМБА | Сумма | Дата последней донации Гаврилова |
        Дата последней донации ФМБА | Контакты соцсети | Телефон
    """

    df = pd.read_excel(file_path)

    # Карта «русский столбец → атрибут модели Donor»
    column_map = {
        "ФИО": "full_name",
        "Группа": "group",
        "Кол-во Гаврилова": "gavrilova_count",
        "Кол-во ФМБА": "fmba_count",
        "Сумма": "total_sum",
        "Дата последней донации Гаврилова": "last_gavrilova",
        "Дата последней донации ФМБА": "last_fmba",
        "Контакты соцсети": "social",
        "Телефон": "phone",
    }

    # Переименовываем колонки, если совпадают
    df = df.rename(columns={ru: attr for ru, attr in column_map.items() if ru in df.columns})

    imported_count = 0
    updated_count = 0

    for _, row in df.iterrows():
        # Требуем хотя бы телефон для идентификации
        phone = row.get("phone") if "phone" in row else row.get("Телефон")
        if pd.isna(phone):  # type: ignore[arg-type]
            continue

        donor = (await session.execute(select(Donor).where(Donor.phone == phone))).scalars().first()

        # Строим словарь данных, учитывая только поля, реально существующие в модели
        new_data = {}
        for col, value in row.items():
            attr = str(col)
            if pd.isna(value) or not hasattr(Donor, attr):
                continue
            # Приведение типов для дат (pandas.Timestamp → date)
            if attr in {"last_gavrilova", "last_fmba"} and not pd.isna(value):
                value = pd.to_datetime(value).date()
            new_data[attr] = value

        if donor:
            for k, v in new_data.items():
                setattr(donor, k, v)
            updated_count += 1
        else:
            # Если нет tg_id – создаём заглушку 0 (можно потом обновить)
            new_data.setdefault("tg_id", 0)
            donor = Donor(**new_data)  # type: ignore[arg-type]
            session.add(donor)
            imported_count += 1

    await session.commit()
    return f"Импортировано {imported_count} записей, обновлено {updated_count}."


# --------- helpers ---------


async def donor_stats(session: AsyncSession, tg_id: int) -> str:
    """Подсчитать количество регистраций и пришедших донаций."""
    total_regs = await session.execute(
        select(Registration).join(Donor).where(Donor.tg_id == tg_id)
    )
    regs = total_regs.scalars().all()
    total = len(regs)
    donated = sum(1 for r in regs if r.status == "donated")
    noshow = sum(1 for r in regs if r.status == "no-show")
    return (
        f"Всего записей: {total}\n"
        f"Пришел: {donated}\n"
        f"No-show: {noshow}"
    )


async def get_donor_active_registration(session: AsyncSession, tg_id: int) -> Optional[Registration]:
    """Находит ближайшую активную регистрацию донора."""
    result = await session.execute(
        select(Registration)
        .join(Donor)
        .join(Event)
        .where(Donor.tg_id == tg_id)
        .where(Registration.status == "registered")
        .where(Event.date >= date.today())
        .order_by(asc(Event.date))  # type: ignore[arg-type]
    )
    return result.scalars().first()


# ======== Геймификация: очки, уровни, серии ========

_LEVELS: list[tuple[int, str]] = [
    (0, "Бронзовый"),
    (400, "Серебряный"),
    (900, "Золотой"),
    (1500, "Платиновый"),
]

_STREAK_RESET_DAYS = 120  # если до следующей донации прошло больше – серия обнуляется
_POINTS_PER_DONATION = 100


def compute_level(points: int) -> str:
    """Возвращает название уровня на основе числа очков."""
    level = _LEVELS[0][1]
    for threshold, name in _LEVELS:
        if points >= threshold:
            level = name
        else:
            break
    return level


async def apply_successful_donation(
    session: AsyncSession,
    donor: "Donor",
    event_date: "date",
    blood_center: str | None = None,
) -> None:
    """Обновляет статистику и очки донора после подтверждения донации.

    * Увеличивает `total_sum`
    * Начисляет очки
    * Рассчитывает серию (streak)
    """
    from sqlalchemy.ext.asyncio import AsyncSession as _AS
    assert isinstance(session, _AS)

    # 0) update per‐center statistics if blood_center provided
    if blood_center:
        from donor_bot.utils.center import normalize_center_name

        canonical = normalize_center_name(blood_center)
        if canonical == "ЦК ФМБА":
            donor.fmba_count = (donor.fmba_count or 0) + 1
            donor.last_fmba = event_date
        elif canonical == "ЦК им. О.К. Гаврилова":
            donor.gavrilova_count = (donor.gavrilova_count or 0) + 1
            donor.last_gavrilova = event_date

    # 1) total donations counter
    donor.total_sum = (donor.total_sum or 0) + 1

    # 2) streak calculation
    if donor.last_donation:
        diff = (event_date - donor.last_donation).days
        if diff <= _STREAK_RESET_DAYS:
            donor.streak = (donor.streak or 0) + 1
        else:
            donor.streak = 1
    else:
        donor.streak = 1

    donor.last_donation = event_date

    # 3) points (базовые + бонус за серию)
    bonus = max(donor.streak - 1, 0) * 10  # +10 очков за каждый шаг серии, начиная со 2-й
    donor.points = (donor.points or 0) + _POINTS_PER_DONATION + bonus

    session.add(donor)
    # Коммит выполняет вызывающая функция, чтобы не ломать bulk-import


async def get_year_leaderboard(session: AsyncSession, year: int, limit: int = 10):
    """Top‐donors of the given year (by количество донаций).  Returns list[(Donor, donations)]."""
    from donor_bot.models import Registration, Event, Donor  # локальный импорт

    regs = (
        await session.execute(
            select(Registration, Event, Donor)
            .join(Event, Registration.event_id == Event.id)  # type: ignore[arg-type]
            .join(Donor)  # type: ignore[arg-type]
            .where(Registration.status == "donated")
        )
    ).all()

    counters: dict[int, int] = {}
    donor_map: dict[int, Donor] = {}
    for reg, ev, d in regs:  # type: ignore[misc]
        if ev.date.year != year:
            continue
        counters[d.id] = counters.get(d.id, 0) + 1  # type: ignore[arg-type]
        donor_map[d.id] = d  # type: ignore[arg-type]

    # sort and limit
    top = sorted(counters.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [(donor_map[donor_id], count) for donor_id, count in top]
